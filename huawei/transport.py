"""
蓝牙传输层（华为 / HONOR）。

- 华为耳机走 **Bluetooth Classic RFCOMM / SPP**，RFCOMM 通道号**固定为 1**
  （见上游 per_model 驱动的 `self._spp_service_port = 1`），**不是**按服务
  UUID 做 SDP 查找 —— 这一点与 vivo 版有本质区别。
- Windows：提供两条路径，优先用 Python 标准库的 `socket.AF_BLUETOOTH` +
  `BTPROTO_RFCOMM`（3.9+ 在 Windows 上可用时），否则回退到 ctypes 调
  `ws2_32.dll` 直接建 RFCOMM socket（与 vivo 版同一套 winsock 封装，只是
  serviceClassId 置空、port 固定 1）。两条路径都零第三方依赖。
- 其它平台：本模块仍可正常 import（只有在真正连接时才报平台错误），
  便于在非 Windows 下跑协议测试与演示模式。

已配对设备枚举复用工程既有的 `bthprops.cpl` 那套（`BluetoothFindFirstDevice`
/ `BluetoothFindNextDevice` / `BLUETOOTH_DEVICE_INFO`），注册表作为兜底。
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

IS_WINDOWS = sys.platform == "win32"


def _module_has_af_bluetooth() -> bool:
    return hasattr(socket, "AF_BLUETOOTH") and hasattr(socket, "BTPROTO_RFCOMM")


HAS_STDLIB_BLUETOOTH = _module_has_af_bluetooth()


# --------------------------------------------------------------------------- #
# 抽象接口（与 vivo 版逐字对齐）
# --------------------------------------------------------------------------- #

class Transport(ABC):
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def send(self, data: bytes) -> None: ...

    @abstractmethod
    def recv(self, timeout: float = 1.0) -> bytes: ...

    @abstractmethod
    def close(self) -> None: ...

    @property
    def connected(self) -> bool:
        return False


# --------------------------------------------------------------------------- #
# Windows：ctypes ws2_32 实现（RFCOMM，地址 + 固定通道）
# --------------------------------------------------------------------------- #

if IS_WINDOWS:
    import ctypes
    import winreg

    AF_BTH = 32
    SOCK_STREAM = 1
    BTHPROTO_RFCOMM = 3
    SOL_SOCKET = 0xFFFF
    SO_RCVTIMEO = 0x1006
    SO_SNDTIMEO = 0x1005

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_uint32),
            ("Data2", ctypes.c_uint16),
            ("Data3", ctypes.c_uint16),
            ("Data4", ctypes.c_uint8 * 8),
        ]

    class _SOCKADDR_BTH(ctypes.Structure):
        _pack_ = 1
        _fields_ = [
            ("addressFamily", ctypes.c_ushort),
            ("btAddr", ctypes.c_ulonglong),
            ("serviceClassId", _GUID),
            ("port", ctypes.c_uint32),
        ]

    _ws = ctypes.WinDLL("ws2_32.dll")
    _ws.WSAStartup.argtypes = [ctypes.c_uint16, ctypes.c_void_p]
    _ws.WSAStartup.restype = ctypes.c_int
    _ws.WSAGetLastError.restype = ctypes.c_int
    _ws.socket.restype = ctypes.c_void_p
    _ws.socket.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
    _ws.connect.restype = ctypes.c_int
    _ws.connect.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
    _ws.closesocket.restype = ctypes.c_int
    _ws.closesocket.argtypes = [ctypes.c_void_p]
    _ws.send.restype = ctypes.c_int
    _ws.send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    _ws.recv.restype = ctypes.c_int
    _ws.recv.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    _ws.setsockopt.restype = ctypes.c_int
    _ws.setsockopt.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int
    ]

    _wsa_started = False
    _wsa_lock = threading.Lock()

    def _ensure_wsa() -> None:
        global _wsa_started
        with _wsa_lock:
            if not _wsa_started:
                _ws.WSAStartup(0x0202, (ctypes.c_ubyte * 400)())
                _wsa_started = True

    def _empty_guid() -> "_GUID":
        return _GUID(0, 0, 0, (ctypes.c_uint8 * 8)())

    class WindowsRfcommTransport(Transport):
        """按 MAC + RFCOMM 通道号连接华为耳机（通道固定 1）。"""

        def __init__(
            self,
            address: str,
            channel: int = 1,
            profile=None,          # 与 vivo 版签名兼容，华为侧不使用
            service_uuid: str = "",  # 华为不做 SDP 查找，留空
        ) -> None:
            self.address = address
            self.channel = int(channel)
            self.profile = profile
            self.service_uuid = service_uuid
            self._sock = None
            self._lock = threading.Lock()
            self._connected = False

        @property
        def connected(self) -> bool:
            return self._connected

        def connect(self) -> None:
            _ensure_wsa()
            s = _ws.socket(AF_BTH, SOCK_STREAM, BTHPROTO_RFCOMM)
            if not s:
                raise RuntimeError("无法创建蓝牙 socket（ws2_32.dll）")
            sa = _SOCKADDR_BTH()
            sa.addressFamily = AF_BTH
            sa.btAddr = int(self.address.replace(":", ""), 16)
            sa.serviceClassId = _empty_guid()
            sa.port = self.channel           # 固定通道 1（华为约定）
            if _ws.connect(s, ctypes.byref(sa), ctypes.sizeof(sa)) != 0:
                err = _ws.WSAGetLastError()
                _ws.closesocket(s)
                raise RuntimeError(
                    f"RFCOMM 连接失败（WSA 错误 {err}）。请确认耳机已与 Windows 配对、"
                    f"已开机且在范围内，且没有被其他设备独占控制通道（通道 {self.channel}）。"
                )
            ms = ctypes.c_ulong(1000)
            _ws.setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, ctypes.byref(ms), ctypes.sizeof(ms))
            _ws.setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, ctypes.byref(ms), ctypes.sizeof(ms))
            self._sock = s
            self._connected = True

        def send(self, data: bytes) -> None:
            if self._sock is None:
                raise RuntimeError("通道未连接")
            with self._lock:
                _ws.send(self._sock, data, len(data), 0)

        def recv(self, timeout: float = 1.0) -> bytes:
            if self._sock is None:
                return b""
            end = time.time() + timeout
            chunks: List[bytes] = []
            while time.time() < end:
                buf = (ctypes.c_ubyte * 1024)()
                n = _ws.recv(self._sock, buf, 1024, 0)
                if n > 0:
                    chunks.append(bytes(buf[:n]))
                    break
                if n == 0:
                    break
                err = _ws.WSAGetLastError()
                if err in (10060, 10035):      # 超时 / WouldBlock
                    continue
                self._connected = False
                break
            return b"".join(chunks)

        def close(self) -> None:
            self._connected = False
            if self._sock is not None:
                try:
                    _ws.closesocket(self._sock)
                except OSError:
                    pass
            self._sock = None

    # ------------------------------------------------------------------ #
    # 已配对设备枚举：bthprops.cpl 优先，注册表兜底
    # ------------------------------------------------------------------ #

    class _SYSTEMTIME(ctypes.Structure):
        _fields_ = [
            ("wYear", ctypes.c_uint16),
            ("wMonth", ctypes.c_uint16),
            ("wDayOfWeek", ctypes.c_uint16),
            ("wDay", ctypes.c_uint16),
            ("wHour", ctypes.c_uint16),
            ("wMinute", ctypes.c_uint16),
            ("wSecond", ctypes.c_uint16),
            ("wMilliseconds", ctypes.c_uint16),
        ]

    class _BLUETOOTH_DEVICE_INFO(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_uint32),
            ("address", ctypes.c_uint64),
            ("ulClassofDevice", ctypes.c_uint32),
            ("fConnected", ctypes.c_int),
            ("fRemembered", ctypes.c_int),
            ("fAuthenticated", ctypes.c_int),
            ("stLastSeen", _SYSTEMTIME),
            ("stLastUsed", _SYSTEMTIME),
            ("szName", ctypes.c_wchar * 248),
        ]

    class _BLUETOOTH_DEVICE_SEARCH_PARAMS(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_uint32),
            ("fReturnAuthenticated", ctypes.c_int),
            ("fReturnRemembered", ctypes.c_int),
            ("fReturnUnknown", ctypes.c_int),
            ("fReturnConnected", ctypes.c_int),
            ("fIssueInquiry", ctypes.c_int),
            ("cTimeoutMultiplier", ctypes.c_ubyte),
            ("hRadio", ctypes.c_void_p),
        ]

    try:
        _bth = ctypes.WinDLL("bthprops.cpl")
        _bth.BluetoothFindFirstDevice.argtypes = [
            ctypes.POINTER(_BLUETOOTH_DEVICE_SEARCH_PARAMS),
            ctypes.POINTER(_BLUETOOTH_DEVICE_INFO),
        ]
        _bth.BluetoothFindFirstDevice.restype = ctypes.c_void_p
        _bth.BluetoothFindNextDevice.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(_BLUETOOTH_DEVICE_INFO)
        ]
        _bth.BluetoothFindNextDevice.restype = ctypes.c_int
        _bth.BluetoothFindDeviceClose.argtypes = [ctypes.c_void_p]
        _bth.BluetoothFindDeviceClose.restype = ctypes.c_int
        _BTH_AVAILABLE = True
    except OSError:
        _BTH_AVAILABLE = False

    def _bth_addr_to_mac(addr: int) -> str:
        return ":".join(f"{(addr >> (8 * i)) & 0xFF:02X}" for i in range(5, -1, -1))

    def _enumerate_paired_bthapi() -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        if not _BTH_AVAILABLE:
            return out
        search = _BLUETOOTH_DEVICE_SEARCH_PARAMS()
        search.dwSize = ctypes.sizeof(_BLUETOOTH_DEVICE_SEARCH_PARAMS)
        search.fReturnAuthenticated = 1
        search.fReturnRemembered = 1
        search.fReturnConnected = 1
        search.fReturnUnknown = 0
        search.fIssueInquiry = 0
        search.hRadio = None
        info = _BLUETOOTH_DEVICE_INFO()
        info.dwSize = ctypes.sizeof(_BLUETOOTH_DEVICE_INFO)
        h = _bth.BluetoothFindFirstDevice(ctypes.byref(search), ctypes.byref(info))
        if not h:
            return out
        try:
            while True:
                mac = _bth_addr_to_mac(info.address)
                name = (info.szName or "").strip()
                out.append((name or mac, mac))
                info = _BLUETOOTH_DEVICE_INFO()
                info.dwSize = ctypes.sizeof(_BLUETOOTH_DEVICE_INFO)
                if not _bth.BluetoothFindNextDevice(h, ctypes.byref(info)):
                    break
        finally:
            _bth.BluetoothFindDeviceClose(h)
        return out

    _BTH_KEY = r"SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Devices"

    def _decode_reg_name(raw) -> str:
        if isinstance(raw, bytes):
            s = raw.decode("utf-8", "replace")
        elif isinstance(raw, str):
            try:
                s = bytes.fromhex(raw).decode("utf-8", "replace")
            except ValueError:
                s = raw
        else:
            return ""
        return s.rstrip("\x00").strip()

    def _enumerate_paired_registry() -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        try:
            root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _BTH_KEY)
        except OSError:
            return out
        try:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                if len(sub) != 12:
                    continue
                mac = ":".join(sub[j:j + 2] for j in range(0, 12, 2))
                name = ""
                try:
                    dk = winreg.OpenKey(root, sub)
                    try:
                        for value_name in ("Name", "LEName"):
                            try:
                                v, _ = winreg.QueryValueEx(dk, value_name)
                            except FileNotFoundError:
                                continue
                            name = _decode_reg_name(v)
                            if name:
                                break
                    finally:
                        winreg.CloseKey(dk)
                except OSError:
                    pass
                out.append((name or mac, mac))
        finally:
            winreg.CloseKey(root)
        return out

    def list_paired_devices(
        keywords: Optional[Tuple[str, ...]] = None
    ) -> List[Tuple[str, str]]:
        """返回 [(设备名, MAC), ...]。先 BluetoothFind* API，再注册表兜底。"""
        devices = _enumerate_paired_bthapi()
        if not devices:
            devices = _enumerate_paired_registry()
        if keywords:
            devices = [
                (name, mac) for name, mac in devices
                if any(k.lower() in name.lower() for k in keywords)
            ]
        seen, uniq = set(), []
        for name, mac in devices:
            if mac not in seen:
                seen.add(mac)
                uniq.append((name, mac))
        return uniq

else:
    def list_paired_devices(keywords: Optional[Tuple[str, ...]] = None) -> List[Tuple[str, str]]:
        return []

    class WindowsRfcommTransport(Transport):  # type: ignore
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("RFCOMM 传输仅支持 Windows；当前平台可使用演示模式。")


# --------------------------------------------------------------------------- #
# 若标准库支持 AF_BLUETOOTH，则优先用之（更简洁、无需 ctypes）
# --------------------------------------------------------------------------- #

if HAS_STDLIB_BLUETOOTH:
    class StdlibRfcommTransport(Transport):
        """标准库 socket 版本的 RFCOMM 传输（通道固定 1）。"""

        def __init__(self, address: str, channel: int = 1,
                     profile=None, service_uuid: str = "") -> None:
            self.address = address
            self.channel = int(channel)
            self.profile = profile
            self.service_uuid = service_uuid
            self._sock: Optional[socket.socket] = None
            self._lock = threading.Lock()
            self._connected = False

        @property
        def connected(self) -> bool:
            return self._connected

        def connect(self) -> None:
            s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                              socket.BTPROTO_RFCOMM)
            s.settimeout(5.0)
            s.connect((self.address, self.channel))
            s.settimeout(1.0)
            self._sock = s
            self._connected = True

        def send(self, data: bytes) -> None:
            if self._sock is None:
                raise RuntimeError("通道未连接")
            with self._lock:
                self._sock.sendall(data)

        def recv(self, timeout: float = 1.0) -> bytes:
            if self._sock is None:
                return b""
            try:
                self._sock.settimeout(timeout)
                return self._sock.recv(1024)
            except (socket.timeout, BlockingIOError):
                return b""

        def close(self) -> None:
            self._connected = False
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
            self._sock = None
else:
    StdlibRfcommTransport = None  # type: ignore


def pick_rfcomm_transport():
    """选择可用的 RFCOMM 传输实现：Windows→ctypes，其余平台→标准库。"""
    if IS_WINDOWS:
        return WindowsRfcommTransport
    if HAS_STDLIB_BLUETOOTH:
        return StdlibRfcommTransport
    return WindowsRfcommTransport
