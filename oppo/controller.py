"""
OPPO / 一加 / realme 耳机会话控制器。

对外信号面与 vivo / 华为控制器**逐字一致**，因此 `pods/hub.py` 和
`ui/flyout.py` 不需要为 OPPO 写分支：

    connected / status / battery / noise_mode / spatial / game_mode /
    eq_mode / wear / firmware / multipoint / error / log

能力（降噪、空间音频、游戏模式）不按型号猜，由耳机实际回包决定：
  * `0x810C` 降噪模式 → 只要不是「仅关闭」，就补出「出声模式」分区；
  * `0x812A` 空间音频 → 回包即支持，UI 补出开关；
  * `0x810D` 功能开关 → 6 低延迟、27 空间音效 V1、59 头部追踪。

这样避免了「按名字猜能力」最容易出的两类错：把 Air3 当成有 ANC、
把 X4 的空间音频藏掉。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import List, Optional

from PySide6.QtCore import QObject, Signal

from . import oppo_protocol as op
from . import oppo_features as of
from .demo_transport import DemoTransport
from .oppo_models import display_name, find_model, is_family_name
from .transport import OppoRfcommTransport, Transport, list_paired_devices
from vivo import bt_local

UNKNOWN = -1

#: 音效预设（OPPO 官方 App 的「大师调音」；不同型号子集不同）
EQ_PRESETS = [
    (0, "丹拿原声"),
    (1, "清亮高音"),
    (2, "纯享人声"),
    (3, "澎湃低音"),
    (7, "丹拿高解析"),
]


@dataclass
class DeviceInfo:
    name: str
    mac: str
    is_demo: bool = False


class EarbudController(QObject):
    connected = Signal(bool)
    status = Signal(str)
    battery = Signal(int, bool, int, bool, int, bool)
    noise_mode = Signal(int)                  # 0 降噪 / 1 关闭 / 2 通透
    spatial = Signal(int)
    game_mode = Signal(int)
    eq_mode = Signal(int)
    wear = Signal(str, str)
    firmware = Signal(str)
    multipoint = Signal(list)
    error = Signal(str)
    log = Signal(str)

    #: 供 UI 判断品牌（标题前缀、充电推导等）
    vendor = "oppo"
    #: OPPO 上佩戴状态是左右分立的
    wear_aggregate = False
    #: 多设备列表操作未实现（UI 不会显示该分区，因为 features 里没有 dual）
    multipoint_experimental = False

    def __init__(self) -> None:
        super().__init__()
        self.transport: Optional[Transport] = None
        self.device: Optional[DeviceInfo] = None
        self.features = of.base()
        self.eq_options = list(EQ_PRESETS)
        self._decoder = op.Decoder()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._send_lock = threading.Lock()
        self._is_connected = False
        self._spatial_seen = False
        self._spatial_v2 = True
        self._want_spatial = False

    # ------------------------------------------------------------------ #
    # 设备发现
    # ------------------------------------------------------------------ #
    def discover(self) -> List[DeviceInfo]:
        try:
            paired = list_paired_devices(
                keywords=("oppo", "oneplus", "realme", "enco"))
            out = [DeviceInfo(n, m) for n, m in paired
                   if find_model(n) or is_family_name(n)]
            return out
        except PermissionError as exc:
            self.error.emit(str(exc))
            return []
        except Exception as exc:
            self.log.emit(f"设备枚举不可用：{exc}")
            return []

    # ------------------------------------------------------------------ #
    # 连接管理
    # ------------------------------------------------------------------ #
    def connect_demo(self, name: str = "OPPO Enco X4（演示）") -> None:
        self._start(DemoTransport(name), DeviceInfo(name, "DEMO:00:00", True))

    def connect_device(self, info: DeviceInfo) -> None:
        self._start(OppoRfcommTransport(info.mac), info)

    def disconnect(self) -> None:
        self._stop.set()
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

    def _start(self, transport: Transport, device: DeviceInfo) -> None:
        if self._thread and self._thread.is_alive():
            self.disconnect()
            self._thread.join(timeout=2)
        self.transport = transport
        self.device = device
        self.features = of.base()
        self._decoder.reset()
        self._stop.clear()
        self._spatial_seen = False
        self._spatial_v2 = True
        self._want_spatial = False
        self.status.emit("连接中…")
        self._thread = threading.Thread(target=self._run, name="oppo-earbud",
                                        daemon=True)
        self._thread.start()

    def _run(self) -> None:
        assert self.transport is not None
        try:
            self.transport.connect()
        except Exception as exc:
            self.error.emit(f"连接失败：{exc}")
            self.status.emit("连接失败")
            self._is_connected = False
            self.connected.emit(False)
            return
        self._set_connected(True)
        self.status.emit(
            f"已连接 · {display_name(self.device.name if self.device else None)}")
        self.log.emit(f"已连接 {self.device.name}（{self.device.mac}），"
                      f"协议=OPO v1 RFCOMM")
        sender = threading.Thread(target=self._sender_loop, name="oppo-sender",
                                  daemon=True)
        sender.start()
        while not self._stop.is_set():
            try:
                chunk = self.transport.recv(0.6)
            except Exception as exc:
                self.error.emit(f"蓝牙读取异常：{exc}")
                break
            if not chunk:
                if not self.transport.connected:
                    break
                continue
            for fr in self._decoder.offer(chunk):
                try:
                    self._on_frame(fr)
                except Exception as exc:  # 单帧失败不能杀线程
                    self.log.emit(f"帧处理异常：{exc}")
        self._set_connected(False)
        self.status.emit("已断开")

    # ------------------------------------------------------------------ #
    # 初始化 / 周期查询
    # ------------------------------------------------------------------ #
    def _sender_loop(self) -> None:
        for cmd in op.initial_commands():
            if self._stop.is_set():
                return
            self._send(cmd)
            time.sleep(0.18)
        # 给耳机 0.8s 回 0x812A：没有回包说明这一代只支持「功能开关 27」
        if self._stop.wait(0.8):
            return
        if not self._spatial_seen:
            self._spatial_v2 = False
            self._send(op.cmd_switch_get((op.SWITCH_SPATIAL_V1,
                                          op.SWITCH_HEAD_TRACK)))
        while not self._stop.is_set():
            if self._stop.wait(30):
                return
            self._send(op.cmd_battery())
            self._send(op.cmd_noise_get())
            self._send(op.cmd_spatial_get())

    # ------------------------------------------------------------------ #
    # 收帧分发
    # ------------------------------------------------------------------ #
    def _on_frame(self, fr: op.Frame) -> None:
        cmd = fr.cmd
        p = fr.payload

        # 耳机主动索要主机时间：必须应答，否则部分型号不推状态
        if cmd == 0x0500:
            self._send(op.cmd_host_time())
            return
        if cmd == 0x051C:
            self.log.emit("耳机请求主机能力（暂不应答）")
            return

        if cmd == 0x8100:
            self.log.emit(f"握手成功，能力位图={p[1:].hex(' ')}")
            return

        if cmd == 0x8106 or cmd == 0x0201:
            body = p[1:] if cmd == 0x0201 else p
            b = op.parse_battery(body)
            if b is not None:
                self.battery.emit(
                    b.left if b.left is not None else UNKNOWN, b.left_charging,
                    b.right if b.right is not None else UNKNOWN, b.right_charging,
                    b.case if b.case is not None else UNKNOWN, b.case_charging)
            return

        if cmd == 0x8109 or cmd == 0x0202:
            body = p[1:] if cmd == 0x0202 else p
            w = op.parse_wear(body)
            if w is not None:
                self.wear.emit(w[0], w[1])
            return

        if cmd == 0x810C or cmd == 0x0203:
            body = p[1:] if cmd == 0x0203 else p
            self._handle_noise(body)
            return

        if cmd == 0x812A:
            self._spatial_seen = True
            self._spatial_v2 = True
            mode = op.parse_spatial(p)
            if mode is not None:
                self._grant(of.SPATIAL)
                self.spatial.emit(1 if mode else 0)
            return

        if cmd == 0x8422:
            if op.is_success(fr):
                self._spatial_seen = True
                self._spatial_v2 = True
            else:
                # 这一代没有 0x0422（空间音频 V2），退回功能开关 27
                self._spatial_v2 = False
                self.log.emit(f"0x0422 写入被拒（{op.status_text(fr.status)}），"
                              f"改用功能开关 27")
                self._send(op.cmd_switch_set(
                    op.SWITCH_SPATIAL_V1, bool(self._want_spatial)))
            return

        if cmd == 0x810D or cmd == 0x0503:
            body = p if cmd == 0x810D else (p[1:] if p and p[0] == 0 else p)
            self._handle_switches(body)
            return

        if cmd == 0x810F:
            preset = op.parse_eq(p)
            if preset is not None:
                self.eq_mode.emit(int(preset))
            return
        if cmd == 0x0504:
            if p:
                self.eq_mode.emit(int(p[0]))
            return

        if cmd == 0x8105:
            ver = op.parse_firmware(p)
            if ver:
                self.firmware.emit(ver)
            return

    def _handle_noise(self, payload: bytes) -> None:
        raw = op.parse_noise_index(payload)
        mode = op.parse_noise(payload)
        # 只有真的出现降噪 / 通透档位，才算这只耳机有 ANC
        if raw is not None and raw not in (0, 3):
            self._grant(of.NOISE)
        if mode is not None:
            self.noise_mode.emit(int(mode))

    def _handle_switches(self, payload: bytes) -> None:
        values = op.parse_switch_values(payload)
        if not values:
            return
        if op.SWITCH_GAME in values:
            self._grant(of.GAME)
            self.game_mode.emit(1 if values[op.SWITCH_GAME] else 0)
        if op.SWITCH_SPATIAL_V1 in values:
            self._grant(of.SPATIAL)
            if not self._spatial_seen:
                self.spatial.emit(1 if values[op.SWITCH_SPATIAL_V1] else 0)
        if op.SWITCH_HEAD_TRACK in values and values[op.SWITCH_HEAD_TRACK]:
            # 有头部追踪就一定支持空间音频（此时没有 V2 回包也认）
            self._grant(of.SPATIAL)

    def _grant(self, *caps: str) -> None:
        new = set(self.features.supported) | set(caps)
        if new == set(self.features.supported):
            return
        self.features = of.FeatureSet(frozenset(new))
        self.log.emit(f"能力发现：+{'/'.join(sorted(set(caps)))} → "
                      f"{sorted(self.features.supported)}")

    # ------------------------------------------------------------------ #
    # 控制指令
    # ------------------------------------------------------------------ #
    def _send(self, data: bytes) -> None:
        if self.transport is None or not self._is_connected:
            self.log.emit("未连接，指令已忽略")
            return
        try:
            with self._send_lock:
                self.transport.send(data)
        except Exception as exc:
            self.error.emit(f"发送失败：{exc}")

    def set_noise(self, mode: int) -> None:
        self._send(op.cmd_noise_set(int(mode)))
        # 立刻回读一次：写入是否生效由耳机自己回答，不靠界面猜
        self._send(op.cmd_noise_get())

    def set_spatial(self, on: bool) -> None:
        self._want_spatial = bool(on)
        if self._spatial_v2:
            self._send(op.cmd_spatial_set(1 if on else 0))
            self._send(op.cmd_spatial_get())
        else:
            self._send(op.cmd_switch_set(op.SWITCH_SPATIAL_V1, bool(on)))
            self._send(op.cmd_switch_get((op.SWITCH_SPATIAL_V1,)))

    def set_game(self, on: bool) -> None:
        self._send(op.cmd_switch_set(op.SWITCH_GAME, bool(on)))
        self._send(op.cmd_switch_get((op.SWITCH_GAME,)))

    def set_eq(self, code: int) -> None:
        self._send(op.cmd_eq_set(int(code)))
        self._send(op.cmd_eq_get())

    def find(self, start: bool) -> None:
        self._send(op.cmd_find(bool(start)))

    def set_multi_connect_status(self, mac: str, connect: bool) -> None:
        self.log.emit("OPPO 暂不支持多设备列表的断开 / 连接操作")

    # ------------------------------------------------------------------ #
    # 本机蓝牙侧（与品牌无关，沿用 vivo 的实现）
    # ------------------------------------------------------------------ #
    def local_mac_set(self) -> set:
        out = set()
        try:
            for r in bt_local.list_radios():
                mac = (r.get("mac") or "").upper()
                if mac:
                    out.add(mac)
        except Exception:
            pass
        return out

    def is_local_device(self, name: str, mac: str = "") -> bool:
        m = (mac or "").replace("-", ":").upper()
        if m and m in self.local_mac_set():
            return True
        n = (name or "").strip().lower()
        if not n:
            return False
        if "这台电脑" in n or "本机" in n:
            return True
        try:
            import socket
            host = socket.gethostname().strip().lower()
            if host and host in n:
                return True
        except Exception:
            pass
        return False

    def local_disconnect(self, mac: str) -> bool:
        res = bt_local.disconnect_local(mac)
        self.log.emit(f"[本机断开] {res.detail}")
        return bool(res.ok)

    def local_connect(self, mac: str) -> bool:
        res = bt_local.connect_local(mac)
        self.log.emit(f"[本机连接] {res.detail}")
        return bool(res.ok)

    def _set_connected(self, value: bool) -> None:
        if self._is_connected != value:
            self._is_connected = value
            self.connected.emit(value)
