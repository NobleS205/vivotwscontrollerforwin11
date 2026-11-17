"""
演示传输：在没有真机（或非 Windows 开发环境）时完整模拟一只
「HUAWEI FreeBuds Pro 5」，让 UI 与会话状态机可以全流程跑通。

与 vivo 演示版（帧级内存回环）不同，这里的模拟对象是 **上游属性仓库**：
  * `recv()` 返回的是 JSON 负载的状态包（模拟驱动周期性上报 / 初始查询应答）；
  * `set_property(group, prop, value)` 直接改模拟状态并触发一次上报，
    模拟上游 `driver.set_property()` 后回调的效果。

**注意**：这里**不实现**华为协议帧编解码 —— 那属于上游 vendor 的职责；
演示层只保证 UI 看到的信号流与真机（经 DriverBridge + 上游驱动）一致。
"""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Optional

from .transport import Transport


class DemoTransport(Transport):
    """模拟 HUAWEI FreeBuds Pro 5（上游属性仓库语义）。"""

    def __init__(self, name: str = "HUAWEI FreeBuds Pro 5（演示）") -> None:
        self.device_name = name
        self._rx: "queue.Queue[bytes]" = queue.Queue()
        self._connected = False
        self._lock = threading.Lock()
        self._state = {
            # battery：与上游 OfbHuaweiBatteryHandler 写入的 key 一致
            "battery": {"left": 85, "right": 94, "case": 72,
                        "is_charging": "false"},
            # anc：与上游 OfbHuaweiAncHandler.mode_options 一致（字符串语义）
            "anc_mode": "awareness",
            # eq：上游逻辑名（5=default）
            "eq_preset": "equalizer_preset_default",
            # 佩戴：上游 StateInEar 是 JSON bool（true=双耳佩戴）
            "in_ear": "true",
            # 低延迟：上游 low_latency 是 "true"/"false"
            "low_latency": "true",
            # 双连：上游 dual_connect devices 是 dict[mac, row]，row 含
            # name/auto_connect/preferred/connected/playing
            "dc_devices": {
                "aabbccddeeff": {"name": "HUAWEI Mate 60 Pro",
                                 "auto_connect": True, "preferred": True,
                                 "connected": True, "playing": True},
                "112233445566": {"name": "这台电脑",
                                 "auto_connect": True, "preferred": False,
                                 "connected": True, "playing": False},
            },
            "dc_enabled": "true",
            # 固件：info.software_ver（上游 OfbHuaweiInfoHandler）
            "info": {"software_ver": "1.0.0.182", "device_model": "FreeBuds Pro 5"},
        }

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        self._connected = True
        # 连上后先推一包初始状态（模拟驱动 start() 后的首次查询结果）
        self._emit_state(delay=0.15)

    def close(self) -> None:
        self._connected = False

    def send(self, data: bytes) -> None:
        # 演示层没有帧协议；真实发送只存在于真机路径。这里忽略。
        pass

    def recv(self, timeout: float = 0.5) -> bytes:
        try:
            return self._rx.get(timeout=timeout)
        except queue.Empty:
            return b""

    # ------------------------------------------------------------------ #
    # 状态推送
    # ------------------------------------------------------------------ #
    def _emit_state(self, delay: float = 0.0) -> None:
        payload = json.dumps({"type": "state", "data": self._state}).encode("utf-8")
        if delay:
            time.sleep(delay)
        self._rx.put(payload)

    def set_property(self, group: str, prop: str, value: str) -> None:
        """模拟上游 driver.set_property()：改状态 + 立即回推一包状态。"""
        with self._lock:
            self._apply_set(group, prop, value)
        self._emit_state(delay=0.05)

    # ------------------------------------------------------------------ #
    def _apply_set(self, group: str, prop: str, value: str) -> None:
        s = self._state
        if group == "anc" and prop == "mode":
            # value 为上游语义字符串
            if value in ("cancellation", "normal", "awareness"):
                s["anc_mode"] = value
        elif group == "config" and prop == "low_latency":
            s["low_latency"] = "true" if value == "true" else "false"
        elif group == "sound" and prop == "equalizer_preset":
            # value 为上游逻辑名
            s["eq_preset"] = value
        elif group == "dual_connect" and str(prop).startswith("devices:"):
            # 双连设备级操作：prop = "devices:<mac>:connected"（上游真实写法）
            parts = str(prop).split(":")
            mac = parts[1] if len(parts) > 1 else ""
            field = parts[2] if len(parts) > 2 else ""
            row = s.get("dc_devices", {}).get(mac)
            if row is None:
                return
            if field == "connected" and value in ("true", "false"):
                row["connected"] = (value == "true")
                if value != "true":
                    row["playing"] = False
            elif field == "auto_connect" and value in ("true", "false"):
                row["auto_connect"] = (value == "true")
        elif group == "dual_connect" and prop == "enabled":
            s["dc_enabled"] = "true" if value == "true" else "false"
        # 其余组忽略（演示覆盖 UI 用到的全部能力）
