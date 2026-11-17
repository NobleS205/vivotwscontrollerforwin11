"""
OPPO 系耳机的 RFCOMM 传输。

OPPO / 一加 / realme 的控制通道是**经典蓝牙 RFCOMM（SPP 风格）**，
不是 BLE GATT（只有 LE Audio 模式才切 GATT）。因此可以直接复用 vivo
那套 `ctypes + ws2_32` 的 RFCOMM 客户端，只是把服务 UUID 换掉：

  * 主 UUID：`0000079A-D102-11E1-9B23-00025B00A5A5`（SDP 服务名 ELNK，
    Enco X4 实测走 RFCOMM 通道 15）
  * 备 UUID：`00001107-D102-11E1-9B23-00025B00A5A5`（旧型号配置里的缺省值）
  * 标准 SPP：`00001101-0000-1000-8000-00805F9B34FB`（部分一加 / realme 机型）

UUID 与通道的组合逐条尝试，第一条成功即返回：不同型号的 SDP 记录不一样，
写死一条会让另一批型号连不上。
"""

from __future__ import annotations

import sys
from typing import List, Optional, Tuple

from vivo.transport import Transport, list_paired_devices  # noqa: F401  (re-export)

IS_WINDOWS = sys.platform == "win32"

#: 主控制服务（Enco X4 实测，通道 15）
OPPO_CONTROL_UUID = "0000079a-d102-11e1-9b23-00025b00a5a5"
#: 旧型号 / 部分 realme 的缺省服务 UUID
OPPO_LEGACY_UUID = "00001107-d102-11e1-9b23-00025b00a5a5"
#: 标准串口服务（兜底）
SPP_UUID = "00001101-0000-1000-8000-00805f9b34fb"

#: (UUID, 通道)；通道 None = 交给 SDP 自己找
ATTEMPTS: Tuple[Tuple[str, Optional[int]], ...] = (
    (OPPO_CONTROL_UUID, None),
    (OPPO_CONTROL_UUID, 15),
    (OPPO_LEGACY_UUID, None),
    (OPPO_LEGACY_UUID, 15),
    (SPP_UUID, None),
)


class OppoRfcommTransport(Transport):
    """按 UUID 列表逐个尝试的 RFCOMM 客户端。"""

    def __init__(self, address: str, attempts: Optional[Tuple] = None) -> None:
        self.address = address
        self._attempts = attempts or ATTEMPTS
        self._inner = None
        self._errors: List[str] = []

    @property
    def connected(self) -> bool:
        return bool(self._inner is not None and self._inner.connected)

    def connect(self) -> None:
        if not IS_WINDOWS:
            raise RuntimeError(
                "OPPO 连接仅支持 Windows；当前平台可使用演示模式。")

        from vivo.transport import WindowsRfcommTransport

        for uuid, channel in self._attempts:
            try:
                t = WindowsRfcommTransport(
                    self.address, service_uuid=uuid, channel=channel)
                t.connect()
                self._inner = t
                return
            except Exception as exc:  # 换下一条组合
                self._errors.append(
                    f"{uuid[:8]}…/{channel or 'SDP'}: {exc}")
        raise RuntimeError(
            "OPPO 控制通道连接失败。请确认耳机已与 Windows 配对并且**正在使用**"
            "（先在系统蓝牙里连上、播放一下声音），且没有被手机 App 独占。"
            "尝试记录：" + "；".join(self._errors[-3:]))

    def send(self, data: bytes) -> None:
        if self._inner is None:
            raise RuntimeError("通道未连接")
        self._inner.send(data)

    def recv(self, timeout: float = 1.0) -> bytes:
        if self._inner is None:
            return b""
        return self._inner.recv(timeout)

    def close(self) -> None:
        if self._inner is not None:
            try:
                self._inner.close()
            except Exception:
                pass
        self._inner = None
