"""
耳机会话控制器：后台 `DriverBridge` 线程负责 RFCOMM 连接与上游 OpenFreebuds
华为驱动（asyncio），本控制器把状态映射为 Qt 信号投递到 UI 线程。

**公开面与 vivo 版逐字对齐**：`DeviceInfo`、`EarbudController`、`UNKNOWN`，
信号与方法签名完全一致 —— Stage 3 时 UI 只需要换 import 路径。

与 vivo 版的差异只在内部实现：
  * 不再是“自定义帧编解码 + 读循环”，而是后台事件循环驱动上游
    `DEVICE_TO_DRIVER_MAP[型号](mac)` + `await driver.start()`；
  * 状态来自 `await driver.get_property(...)`（属性仓库），控制走
    `await driver.set_property(...)`（上游 handler 负责编帧）；
  * 华为的 ANC 语义字符串：`cancellation` / `normal` / `awareness`，
    EQ 是上游逻辑 preset 名，低延迟是 `"true"/"false"`，佩戴是 JSON bool。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QObject, Signal

from . import huawei_protocol as vp
from .bridge import DriverBridge
from .demo_transport import DemoTransport
from .huawei_features import FeatureSet, resolve as resolve_features
from .huawei_models import display_name, find_model
from .transport import Transport, WindowsRfcommTransport, list_paired_devices

# 状态未知时用 -1 传递（Qt 信号不支持 Optional[int]）
UNKNOWN = -1


@dataclass
class DeviceInfo:
    name: str
    mac: str
    is_demo: bool = False


class EarbudController(QObject):
    connected = Signal(bool)
    status = Signal(str)                       # 连接状态文本（连接中/已连接/已断开）
    battery = Signal(int, bool, int, bool, int, bool)  # 左%,左充电,右%,右充电,盒%,盒充电（-1=未知）
    noise_mode = Signal(int)                  # 0 降噪 / 1 关闭 / 2 通透
    spatial = Signal(int)                     # -1 未知 / 0 / 1
    game_mode = Signal(int)
    eq_mode = Signal(int)
    wear = Signal(str, str)                   # left/right: worn / in_case / removed
    firmware = Signal(str)
    multipoint = Signal(list)                 # 多设备（双连）[{name,status,active}, ...]
    error = Signal(str)
    log = Signal(str)

    # --- 供 UI 判断「这是谁」的元数据（vivo 版没有这些字段，UI 全部用
    #     getattr 带默认值读取，因此不会影响 vivo 路径） ------------------- #
    vendor = "huawei"
    #: 华为上游只上报「双耳是否在耳」一个布尔，佩戴文案不能写成「双耳」
    wear_aggregate = True
    #: 双连是上游正式 handler（非实验命令），UI 不显示「实验」角标
    multipoint_experimental = False

    def __init__(self) -> None:
        super().__init__()
        self.transport: Optional[Transport] = None
        self.device: Optional[DeviceInfo] = None
        self.features: FeatureSet = resolve_features(None)
        self._bridge: Optional[DriverBridge] = None
        self._thread: Optional[threading.Thread] = None
        self._is_connected = False
        # 最近一次状态缓存（供 set_* 在“无新上报”时也能回读当前值）
        self._state: dict = {}

    # ------------------------------------------------------------------ #
    # 设备发现
    # ------------------------------------------------------------------ #
    def discover(self) -> list[DeviceInfo]:
        """列出已配对的华为 / HONOR 设备（Windows 蓝牙 API / 注册表）；非 Windows 返回空。

        不带 keywords 过滤：耳机在系统里可能只报 **短名**（`FreeBuds Pro 5`），
        不含 "huawei" 字样，用 `huawei_models.is_family_name` 判断更可靠。
        """
        try:
            paired = list_paired_devices()
            from .huawei_models import is_supported
            out = []
            for n, m in paired:
                # v3.34：只列上游有驱动类的型号。旧逻辑把任何名字带
                # huawei / honor 的设备都列出来，手机 / 笔记本也在列表里。
                if is_supported(n):
                    out.append(DeviceInfo(n, m))
            return out
        except PermissionError as exc:
            self.error.emit(str(exc))
            return []
        except Exception as exc:  # 非 Windows 等环境
            self.log.emit(f"设备枚举不可用：{exc}")
            return []

    # ------------------------------------------------------------------ #
    # 连接管理
    # ------------------------------------------------------------------ #
    def connect_demo(self, model: str = "HUAWEI FreeBuds Pro 5") -> None:
        # 演示设备名用注册表里的权威型号名，保证能力矩阵 / EQ 预设表等全部按真机路径解析；
        # “（演示）”只体现在状态文本与日志里。
        self._start(DemoTransport(f"{model}（演示）"),
                    DeviceInfo(model, "DEMO:00:00", is_demo=True))

    def connect_device(self, info: DeviceInfo) -> None:
        transport = WindowsRfcommTransport(info.mac, channel=vp.SPP_CHANNEL)
        self._start(transport, info)

    def disconnect(self) -> None:
        if self._bridge is not None:
            self._bridge.stop()
            self._bridge = None
        t = self.transport
        if t is not None:
            try:
                t.close()
            except Exception:
                pass
        self._set_connected(False)
        self.status.emit("已断开")
        self.battery.emit(UNKNOWN, False, UNKNOWN, False, UNKNOWN, False)
        self.spatial.emit(UNKNOWN)
        self.game_mode.emit(UNKNOWN)
        self.multipoint.emit([])
        self.wear.emit("unknown", "unknown")
        self.firmware.emit("")

    def _start(self, transport: Transport, device: DeviceInfo) -> None:
        # 停掉旧会话：真机走 bridge，演示走内存 transport 线程 —— 两条都要收干净，
        # 否则「Pro 5 演示 → FreeClip 2 演示」会留下一个仍在跑的旧读循环
        if self._bridge is not None or (
                self._thread is not None and self._thread.is_alive()):
            self.disconnect()
            if self._thread is not None:
                self._thread.join(timeout=2)
        self._thread = None
        self.transport = transport
        self.device = device
        self.features = resolve_features(device.name)
        self._state = {}
        self._is_connected = False
        # 新会话开始：先把上一台设备的读数清空，避免初始化期间显示旧数据
        self.battery.emit(UNKNOWN, False, UNKNOWN, False, UNKNOWN, False)
        self.wear.emit("unknown", "unknown")
        self.multipoint.emit([])
        self.firmware.emit("")
        self.status.emit("连接中…")

        # 演示模式：走内存回环 transport；真机：走上游驱动桥
        if device.is_demo:
            self._thread = threading.Thread(target=self._run_demo, args=(transport,),
                                            name="huawei-demo", daemon=True)
            self._thread.start()
        else:
            model = find_model(device.name)
            if model is None:
                self.error.emit(f"暂不支持该型号：{device.name}")
                self.status.emit("连接失败")
                self.connected.emit(False)
                return
            self._bridge = DriverBridge(model, device.mac, on_event=self._on_bridge_event)
            self._bridge.start()

    def _on_bridge_event(self, kind: str, payload) -> None:
        """DriverBridge 回调（后台线程）→ 投递到 Qt 信号（线程安全）。"""
        if kind == "connected":
            ok = bool(payload)
            self._set_connected(ok)
            if ok:
                self.status.emit(f"已连接 · {display_name(self.device.name if self.device else None)}")
            else:
                self.status.emit("已断开")
        elif kind == "state":
            self._state = payload if isinstance(payload, dict) else {}
            self._apply_state()
        elif kind == "error":
            self.error.emit(str(payload))
        elif kind == "log":
            self.log.emit(str(payload))

    def _apply_state(self) -> None:
        """把属性仓库扁平 dict 映射为 UI 信号（所有字段 best-effort，缺省不发出）。"""
        s = self._state

        # 电量：battery{global?, left?, right?, case?, is_charging?}
        bat = s.get("battery") or {}
        left = int(bat["left"]) if "left" in bat else UNKNOWN
        right = int(bat["right"]) if "right" in bat else UNKNOWN
        case = int(bat["case"]) if "case" in bat else UNKNOWN
        charging = _as_bool(bat.get("is_charging"))
        self.battery.emit(left, charging, right, charging, case, charging)

        # 降噪
        mode = s.get("anc_mode")
        if mode is not None:
            ui = vp.VendorToNoiseMode.get(str(mode))
            if ui is not None:
                self.noise_mode.emit(int(ui))

        # EQ（UI 序号 ← 上游逻辑 preset 名）
        eq = s.get("eq_preset")
        if eq:
            ui_code = vp.ui_code_from_logical_name(
                self.device.name if self.device else None, str(eq))
            if ui_code is not None:
                self.eq_mode.emit(ui_code)

        # 佩戴：优先用左右耳分立标志（in_ear_patch 的 PerEarWearHandler
        # 会在解析 0x2B25 时额外记录 in_ear_left / in_ear_right），
        # 这样 UI 能区分「左耳摘下」还是「右耳摘下」；只有拿到聚合值的
        # 老型号才退回单布尔语义（此时无法分左右，如实按同一状态发）。
        left_r = s.get("in_ear_left")
        right_r = s.get("in_ear_right")
        if left_r is not None and right_r is not None:
            l_raw = str(left_r).strip().strip('"').lower()
            r_raw = str(right_r).strip().strip('"').lower()
            if l_raw in ("", "unknown", "none") or r_raw in ("", "unknown", "none"):
                self.wear.emit("unknown", "unknown")
            else:
                self.wear.emit("worn" if _as_bool(l_raw) else "removed",
                               "worn" if _as_bool(r_raw) else "removed")
        else:
            in_ear = s.get("in_ear")
            if in_ear is not None:
                raw = str(in_ear).strip().strip('"').lower()
                if raw in ("", "unknown", "none"):
                    # 耳机还没上报过 → 诚实显示「未知」，不谎报「未佩戴」
                    self.wear.emit("unknown", "unknown")
                else:
                    worn = _as_bool(raw)
                    self.wear.emit("worn" if worn else "in_case",
                                   "worn" if worn else "in_case")

        # 低延迟（游戏模式）
        ll = s.get("low_latency")
        if ll is not None:
            self.game_mode.emit(1 if _as_bool(ll) else 0)

        # 多设备（双连）
        dc = s.get("dc_devices")
        if isinstance(dc, dict):
            rows = []
            for mac, d in dc.items():
                if not isinstance(d, dict):
                    continue
                name = d.get("name") or mac
                status = (vp.MC_CONNECTED_ACTIVE if d.get("playing")
                          else vp.MC_CONNECTED_INACTIVE if d.get("connected")
                          else vp.MC_DISCONNECTED)
                rows.append({
                    "name": name,
                    "status": status,
                    "active": bool(d.get("playing")),
                    # 上游 MAC 是无分隔的 12 位小写十六进制，统一成 AA:BB:..:FF，
                    # 与 vivo 侧行数据同构（UI 的断开/连接按钮要用它）
                    "mac": _mac_pretty(mac),
                })
            self.multipoint.emit(rows)

        # 固件
        info = s.get("info") or {}
        fw = info.get("software_ver")
        if fw:
            self.firmware.emit(str(fw))

    # ------------------------------------------------------------------ #
    # 演示模式（内存回环 transport + 上游驱动不可用时的 UI 全流程验证）
    # ------------------------------------------------------------------ #
    def _run_demo(self, transport: Transport) -> None:
        try:
            transport.connect()
        except Exception as exc:
            self.error.emit(f"连接失败：{exc}")
            self.status.emit("连接失败")
            self._set_connected(False)
            return
        self._set_connected(True)
        self.status.emit(f"已连接 · {display_name(self.device.name if self.device else None)}")
        self.log.emit(f"演示模式：{self.device.name}（{self.device.mac}）")

        while transport is self.transport:
            try:
                chunk = transport.recv(0.5)
            except Exception as exc:
                self.error.emit(f"演示读取异常：{exc}")
                break
            if not chunk:
                if not transport.connected:
                    break
                continue
            self._handle_demo_chunk(chunk)

        # 只有「这一条」会话还是当前会话时才广播断开，避免旧线程把新会话打回未连接
        if transport is self.transport:
            self._set_connected(False)
            self.status.emit("已断开")

    def _handle_demo_chunk(self, chunk: bytes) -> None:
        # 演示 transport 直接以「状态 dict」为负载（见 demo_transport.py）
        try:
            payload = json.loads(chunk.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.log.emit(f"演示帧无法解析：{chunk[:32].hex()}")
            return
        kind = payload.get("type")
        if kind == "state":
            self._state = payload.get("data") or {}
            self._apply_state()

    # ------------------------------------------------------------------ #
    # 控制指令（均在 UI 线程被调用；真机走 bridge.set_property，演示走 transport）
    # ------------------------------------------------------------------ #
    def _set(self, group: str, prop: str, value: str) -> None:
        if self.device is not None and self.device.is_demo:
            if self.transport is not None and self._is_connected:
                try:
                    self.transport.set_property(group, prop, value)
                except Exception as exc:
                    self.error.emit(f"发送失败：{exc}")
            else:
                self.log.emit("未连接，指令已忽略")
        elif self._bridge is not None and self._is_connected:
            self._bridge.set_property(group, prop, value)
        else:
            self.log.emit("未连接，指令已忽略")

    def set_noise(self, mode: int) -> None:
        vendor = vp.NoiseModeToVendor.get(vp.NoiseMode(mode))
        if vendor is None:
            self.log.emit(f"未知降噪档位：{mode}")
            return
        self._set("anc", "mode", vendor)

    def set_spatial(self, on: bool) -> None:
        # 华为侧当前无空间音频 handler —— 与能力矩阵一致（has("spatial")=False）
        self.log.emit("该型号不支持空间音频（华为侧暂未接入），指令已忽略")

    def set_game(self, on: bool) -> None:
        self._set("config", "low_latency", "true" if on else "false")

    def set_eq(self, code: int) -> None:
        model = self.device.name if self.device else None
        logical = vp.preset_logical_name_for(model, code)
        if logical is None:
            self.log.emit(f"该型号不支持 EQ 预设 {code}")
            return
        self._set("sound", "equalizer_preset", logical)

    def find(self, start: bool) -> None:
        # 华为侧当前无查找耳机 handler —— 能力矩阵 has("find")=False，UI 已隐藏入口。
        # 保留方法以满足与 vivo 版逐字对齐的公开面；仅记录日志。
        self.log.emit(f"查找耳机：该型号暂不支持（华为侧未接入），start={start}")

    # ------------------------------------------------------------------ #
    # 多设备（双连）控制
    #
    # 与 vivo 侧不同：华为的双连是上游正式 handler，不是「实验命令」——
    # 单台设备的连接/断开由 `dual_connect//devices:<mac>:connected` 下发，
    # 耳机侧会执行 CONNECT / DISCONNECT 并在完成后重新枚举设备表。
    # ------------------------------------------------------------------ #
    def set_multi_connect_status(self, mac: str, connect: bool,
                                 timeout: float = 2.0) -> None:
        target = _mac_plain(mac)
        if not target:
            self.log.emit("缺少设备 MAC，已忽略双连操作")
            return
        self._set("dual_connect", f"devices:{target}:connected",
                  "true" if connect else "false")
        self.log.emit(f"[双连] 已下发 {'连接' if connect else '断开'} {mac}")

    def set_preferred_device(self, mac: str) -> None:
        target = _mac_plain(mac)
        if not target:
            return
        self._set("dual_connect", "preferred_device", target)

    @property
    def eq_options(self) -> list[tuple[int, str]]:
        """当前型号可用的音效选项（UI 用它重建 chip，型号之间选项不同）。"""
        return vp.eq_options(self.device.name if self.device else None)

    def _set_connected(self, value: bool) -> None:
        if self._is_connected != value:
            self._is_connected = value
            self.connected.emit(value)


# --------------------------------------------------------------------------- #
# 内部小工具
# --------------------------------------------------------------------------- #

def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    try:
        return bool(json.loads(s))
    except (TypeError, ValueError):
        return default


def _mac_plain(mac: str) -> str:
    """'AA:BB:CC:DD:EE:FF' / 'aa-bb-..' → 'aabbccddeeff'（上游要求无分隔小写）。"""
    return "".join(c for c in str(mac or "").lower() if c.isalnum())


def _mac_pretty(mac: str) -> str:
    """上游的无分隔 MAC → 'AA:BB:CC:DD:EE:FF'；无法识别时原样返回。"""
    plain = _mac_plain(mac)
    if len(plain) != 12:
        return str(mac or "")
    return ":".join(plain[i:i + 2].upper() for i in range(0, 12, 2))
