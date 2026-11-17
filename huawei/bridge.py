"""
驱动桥：在后台线程里跑一个 asyncio 事件循环，用它去驱动 **未改动的** 上游
OpenFreebuds 华为驱动。

为什么必须这样：上游 `openfreebuds` 是 asyncio 架构（`await driver.start()`
/ `await driver.get_property(...)` / `await driver.set_property(...)`），而 Qt
UI 是同步的。本模块把两者接起来，**不重写任何上游 handler**：

  * Qt 侧（任意线程） → `DriverBridge` → `asyncio.run_coroutine_threadsafe(...)`
    → 后台事件循环 → 上游 driver。

  * 后台事件循环内：`DEVICE_TO_DRIVER_MAP[device_name](mac)` 构造驱动 →
    `await driver.start()` → 之后每 1s 轮询 `await driver.get_property(...)`
    读状态，`await driver.set_property(...)` 下发控制。

传输层：上游 `OfbDriverSppGeneric` 通过
`openfreebuds.driver.generic.spp_transport.get_default_transport()` 拿
`(StreamReader, StreamWriter)`。Windows 的 Python 没有 `AF_BLUETOOTH`，所以
我们在**集成边界**（本文件）把该工厂替换成基于本工程 `huawei/transport.py`
阻塞式 RFCOMM 的实现，并把它适配成 asyncio 的 reader/writer 对象。
全程不改 vendor 源码。
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from typing import Any, Callable, Optional

from ._vendor import ensure_vendor_on_path

ensure_vendor_on_path()

# --- 上游 import 顺序陷阱（实测，非臆测） ---------------------------------- #
# `openfreebuds/driver/__init__.py` 第 3 行 `from openfreebuds.driver.huawei.driver import *`
# 会把 `openfreebuds.driver.huawei.driver.generic`（一个**模块**）作为 `generic`
# 属性挂到 `openfreebuds.driver` 包上，遮蔽了真正的命名空间包
# `openfreebuds/driver/generic/`（`sys.modules["openfreebuds.driver.generic"]` 才是它）。
# 后果：`import openfreebuds.driver.generic.spp` 会沿父包属性解析到 huawei 的
# generic.py 而失败（cannot import name 'spp'）。修复：把遮蔽的属性改回真包。
import openfreebuds.driver as _ofb_driver_pkg  # noqa: E402
_gen_pkg = __import__("openfreebuds.driver.generic", fromlist=["x"])  # 命中 sys.modules 真包
_ofb_driver_pkg.generic = _gen_pkg

# --- 复用上游（不重写） ---------------------------------------------------- #
from openfreebuds.driver.constants import DEVICE_TO_DRIVER_MAP  # noqa: E402
from .transport import pick_rfcomm_transport  # noqa: E402


# --------------------------------------------------------------------------- #
# asyncio reader/writer 适配层（把阻塞 transport 包成上游要的 Stream 接口）
# --------------------------------------------------------------------------- #

class _WriterShim:
    """
    上游只用到 `write()` / `await drain()` / `close()`，故实现这几个即可。

    注意：这是**适配**，不是重写协议 —— 数据字节仍原样交给阻塞 transport 发送。
    """

    def __init__(self, transport) -> None:
        self._t = transport
        self._closed = False

    def write(self, data: bytes) -> None:
        if self._closed:
            return
        self._t.send(bytes(data))

    async def drain(self) -> None:
        return None

    def is_closing(self) -> bool:
        return self._closed

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._t.close()
            except Exception:
                pass

    async def wait_closed(self) -> None:
        return None


class _BlockingSppTransport:
    """
    适配器：把本工程的阻塞式 RFCOMM Transport 变成上游 SPP 驱动需要的
    `async open(address, channel) -> (reader, writer)`。

    读方向用一个守护线程把阻塞 recv 的字节喂进 asyncio.StreamReader。
    """

    def __init__(self, on_log: Optional[Callable[[str], None]] = None) -> None:
        self._on_log = on_log

    async def open(self, address: str, channel: int, *, connect_delay: float = 0):
        loop = asyncio.get_running_loop()
        cls = pick_rfcomm_transport()
        transport = cls(address, channel=channel)
        # 阻塞连接放到线程池，避免卡住事件循环
        await loop.run_in_executor(None, transport.connect)

        reader = asyncio.StreamReader()
        writer = _WriterShim(transport)

        def _reader_thread():
            try:
                while not writer.is_closing():
                    chunk = transport.recv(0.5)
                    if chunk:
                        loop.call_soon_threadsafe(reader.feed_data, chunk)
                    elif not transport.connected:
                        break
            except Exception as exc:  # 读线程异常只记录，不炸循环
                if self._on_log:
                    self._on_log(f"读线程结束：{exc}")
            finally:
                loop.call_soon_threadsafe(reader.feed_eof)

        t = threading.Thread(target=_reader_thread, name="huawei-spp-reader", daemon=True)
        t.start()
        return reader, writer


_ORIGINAL_TRANSPORT_FACTORY = None


def install_transport_factory() -> None:
    """把上游的 get_default_transport 替换为基于本工程阻塞 transport 的实现。"""
    global _ORIGINAL_TRANSPORT_FACTORY
    # 再次修复 generic 属性遮蔽（保证 spp 模块可解析）
    import openfreebuds.driver as _drv_pkg
    _gen = __import__("openfreebuds.driver.generic", fromlist=["x"])
    _drv_pkg.generic = _gen
    import openfreebuds.driver.generic.spp as _spp_mod
    if _ORIGINAL_TRANSPORT_FACTORY is None:
        _ORIGINAL_TRANSPORT_FACTORY = _spp_mod.get_default_transport
    _spp_mod.get_default_transport = lambda: _BlockingSppTransport()


def restore_transport_factory() -> None:
    """还原上游工厂（测试/清理用）。"""
    import openfreebuds.driver as _drv_pkg
    _gen = __import__("openfreebuds.driver.generic", fromlist=["x"])
    _drv_pkg.generic = _gen
    import openfreebuds.driver.generic.spp as _spp_mod
    if _ORIGINAL_TRANSPORT_FACTORY is not None:
        _spp_mod.get_default_transport = _ORIGINAL_TRANSPORT_FACTORY


# --------------------------------------------------------------------------- #
# 状态读取 / 控制下发：只调用上游公开的 get_property / set_property
# --------------------------------------------------------------------------- #

def _json_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    try:
        return bool(json.loads(value))
    except (TypeError, ValueError):
        return default


async def read_state(driver) -> dict:
    """把驱动属性仓库里与 UI 相关的字段读成一个扁平 dict（全部 best-effort）。

    注意字段类型（来自上游 handler 的实际写法，已逐个核对）：
      * `battery` / `info`：dict（battery 里 is_charging 是 JSON 字符串）；
      * `anc`/`mode`、`sound`/`equalizer_preset`、`state`/`in_ear`、
        `config`/`low_latency`：字符串（in_ear、low_latency 是 JSON 布尔）；
      * `dual_connect`/`devices`：**JSON 字符串**（handler 里 json.dumps 过），
        这里就地解析成 dict，避免上层每处都要再 loads 一次。
    """
    battery = await driver.get_property("battery", None, None) or {}
    anc_mode = await driver.get_property("anc", "mode", None)
    eq_preset = await driver.get_property("sound", "equalizer_preset", None)
    in_ear = await driver.get_property("state", "in_ear", None)
    low_latency = await driver.get_property("config", "low_latency", None)
    dc_devices = await driver.get_property("dual_connect", "devices", None)
    dc_enabled = await driver.get_property("dual_connect", "enabled", None)
    dc_preferred = await driver.get_property("dual_connect", "preferred_device", None)
    info = await driver.get_property("info", None, None) or {}

    return {
        "battery": battery if isinstance(battery, dict) else {},
        "anc_mode": anc_mode,
        "eq_preset": eq_preset,
        "in_ear": in_ear,
        "low_latency": low_latency,
        "dc_devices": _as_dict(dc_devices),
        "dc_enabled": dc_enabled,
        "dc_preferred": dc_preferred,
        "info": info if isinstance(info, dict) else {},
    }


def _as_dict(value) -> dict:
    """把上游的 JSON 字符串 / dict 统一成 dict（解析失败返回空 dict）。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


class DriverBridge:
    """
    后台线程 + asyncio 事件循环，驱动一台真实华为耳机。

    线程安全：对外方法可从任意线程调用；内部用 `run_coroutine_threadsafe`。
    """

    def __init__(self, device_name: str, mac: str,
                 on_event: Optional[Callable[[str, Any], None]] = None,
                 poll_interval: float = 1.0) -> None:
        self.device_name = device_name
        self.mac = mac
        self.poll_interval = poll_interval
        self._on_event = on_event
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._driver = None
        self._stop_evt: Optional[asyncio.Event] = None
        self._exceptions: "queue.Queue[BaseException]" = queue.Queue()
        self._connected = False
        self._last_state: Optional[dict] = None

    # -- 生命周期 -------------------------------------------------------- #
    def start(self) -> None:
        install_transport_factory()
        self._thread = threading.Thread(target=self._thread_main,
                                        name="huawei-bridge", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止驱动线程。

        ⚠️ 本方法会**等待**后台线程收尾（最多约 4s），因为它在 UI 线程被调用
        （切换设备 / 退出程序）。等待是必要的：旧会话若还占着同一台耳机的
        RFCOMM 通道，紧接着的新连接会失败。等待时间做了上限保护：
        `_main` 每 1s 检查一次停止事件，正常情况 1s 内返回。
        """
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(lambda: self._stop_evt.set()
                                      if self._stop_evt else None)
        if self._thread is not None:
            self._thread.join(timeout=3)
            # 驱动 init 可能正卡在某个 handler 的超时里：再给事件循环一次机会
            # 退出（线程是 daemon，最坏情况下也不会阻塞进程退出）。
            if self._thread.is_alive() and loop is not None:
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except Exception:
                    pass
                self._thread.join(timeout=1)
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    # -- 事件循环主体 ---------------------------------------------------- #
    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main())
        except BaseException as exc:  # noqa: BLE001
            self._emit("error", f"桥接线程异常：{exc}")
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            self._loop = None

    def _link_lost(self) -> bool:
        """链路是否已经断了。

        判据一：上游 SPP 驱动的接收任务已结束（对端关闭 socket 时它会 return）。
        判据二：Windows 蓝牙表里这台设备已不是 connected —— 耳机放回充电仓
        通常 1~2 秒内就会反映在这一位上，比等 socket 超时快得多。
        """
        drv = self._driver
        if drv is None:
            return True
        # 只在「曾经健康 → 现在不健康」时判定，避免某些驱动（如测试用的
        # fixture）从不设置 started 而被误判成已断开
        try:
            healthy_fn = getattr(drv, "healthy", None)
            healthy = bool(healthy_fn()) if callable(healthy_fn) else None
        except Exception:
            healthy = None
        if healthy:
            self._link_was_healthy = True
        elif healthy is False and getattr(self, "_link_was_healthy", False):
            return True
        # 系统蓝牙查询较重，每 3 轮（约 3s）查一次
        self._bt_probe = getattr(self, "_bt_probe", 0) + 1
        if self._bt_probe % 3:
            return False
        try:
            from pods import btdevices
            mac = (self.mac or "").replace("-", ":").upper()
            if not mac:
                return False
            for d in btdevices.list_bt_devices():
                if d.mac.upper() == mac:
                    return not d.connected
        except Exception:
            pass
        return False

    async def _main(self) -> None:
        self._stop_evt = asyncio.Event()
        cls = DEVICE_TO_DRIVER_MAP.get(self.device_name)
        if cls is None:
            self._emit("error", f"未找到型号驱动：{self.device_name}")
            self._emit("connected", False)
            return

        try:
            self._driver = cls(self.mac)
        except Exception as exc:  # noqa: BLE001
            self._emit("error", f"创建驱动失败：{exc}")
            self._emit("connected", False)
            return

        # 集成层补丁：FreeClip 2 的佩戴通知是 0x2B25（上游只认 0x2B03），
        # 必须在 start() 之前替换 handler —— commands→handler 的映射在
        # _start_all_handlers() 里建立。详见 huawei/in_ear_patch.py。
        try:
            from .in_ear_patch import patch_driver
            if patch_driver(self._driver, self.device_name):
                self._emit("log", f"已应用佩戴状态补丁：{self.device_name}")
        except Exception as exc:  # noqa: BLE001
            self._emit("log", f"佩戴状态补丁未生效（不影响其它功能）：{exc}")

        try:
            await self._driver.start()
        except Exception as exc:  # noqa: BLE001
            self._emit("connected", False)
            self._emit("error", f"连接失败：{exc}")
            return

        self._connected = True
        self._last_state = None
        self._emit("connected", True)

        # 周期轮询状态（1s）：只在**内容变化**时向 UI 投递，避免每秒重发
        # 相同状态（否则多设备列表每秒重建一次，按钮点击会被打断）
        while not self._stop_evt.is_set():
            # 断连检测（v3.26）：耳机放回充电仓 / 关机时，上游读循环会因
            # socket 关闭而结束，但驱动不会主动通知我们 —— 旧版本面板会一直
            # 停在「已连接」。这里每轮检查一次链路，断了就立刻上报。
            if self._link_lost():
                self._emit("log", "链路已断开（耳机放回充电仓或已关机）")
                break
            try:
                state = await read_state(self._driver)
                if state != self._last_state:
                    self._last_state = state
                    self._emit("state", state)
            except Exception as exc:  # noqa: BLE001
                self._emit("log", f"读取状态异常：{exc}")
            try:
                await asyncio.wait_for(self._stop_evt.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass

        try:
            await self._driver.stop()
        except Exception:
            pass
        self._connected = False
        self._emit("connected", False)

    # -- 控制（线程安全） ------------------------------------------------ #
    def set_property(self, group: str, prop: str, value: str,
                     done: Optional[Callable[[Optional[BaseException]], None]] = None) -> None:
        loop = self._loop
        if loop is None or self._driver is None:
            self._emit("log", "未连接，指令已忽略")
            return

        async def _do():
            await self._driver.set_property(group, prop, value)

        def _cb(fut):
            try:
                fut.result()
            except BaseException as exc:  # noqa: BLE001
                self._emit("error", f"下发失败：{exc}")
                if done:
                    done(exc)
                return
            if done:
                done(None)

        asyncio.run_coroutine_threadsafe(_do(), loop).add_done_callback(_cb)

    # -- 事件投递 -------------------------------------------------------- #
    def _emit(self, kind: str, payload: Any) -> None:
        if self._on_event is not None:
            try:
                self._on_event(kind, payload)
            except Exception:
                pass
        if kind == "error":
            self._exceptions.put(RuntimeError(str(payload)))
