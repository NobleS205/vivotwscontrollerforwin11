"""
HuaweiPodsCC: minimal, stdlib-only stand-in for the upstream
``openfreebuds_backend`` package.

Upstream OpenFreebuds keeps its OS/device layer in a separate distribution
(``openfreebuds-backend``).  Its Windows branch imports ``winsdk``, which this
project deliberately does NOT want to depend on.  Only two symbols are actually
touched by the vendored Huawei driver tree:

    - ``get_app_storage_path()``  (used by ``openfreebuds.constants``)
    - ``bt_is_connected(addr)``   (used by ``openfreebuds.driver.generic.base``)

This module provides those two, reusing the project's own ``bthprops.cpl``
based Bluetooth enumeration (see ``huawei/transport.py``).  No third-party
imports, and specifically no ``winsdk``.

This is NOT vendored upstream code -- it is our own replacement.  See
``../../ORIGIN.md`` (section "Not part of upstream / replacements").
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

__all__ = ["get_app_storage_path", "bt_is_connected"]

IS_WINDOWS = sys.platform == "win32"


def get_app_storage_path() -> Path:
    """Return a writable per-user directory (created if missing).

    Windows: ``%LOCALAPPDATA%\\VivoPodsCC``（并入 VivoPodsCC 后统一用同一个
    程序目录，不再单独建 HuaweiPodsCC 文件夹）。
    Other OSes: a subdirectory of the system temp dir.
    """
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    else:
        base = tempfile.gettempdir()
    path = Path(base) / "VivoPodsCC"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        path = Path(tempfile.gettempdir())
    return path


async def bt_is_connected(address: str) -> bool:
    """Return True if the Bluetooth device with ``address`` is connected.

    Implemented with the same ``bthprops.cpl`` (BluetoothFindFirstDevice /
    ``BLUETOOTH_DEVICE_INFO.fConnected``) enumeration used by
    ``huawei/transport.py``.  Returns False on non-Windows platforms or on any
    failure.
    """
    if not IS_WINDOWS or not address:
        return False
    try:
        from ..transport import is_paired_device_connected  # type: ignore
        return bool(is_paired_device_connected(address))
    except Exception:
        # Fall back to a direct, self-contained enumeration.
        try:
            return _bt_is_connected_bthprops(address)
        except Exception:
            return False


def _bt_is_connected_bthprops(address: str) -> bool:
    import ctypes

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

    bth = ctypes.WinDLL("bthprops.cpl")
    bth.BluetoothFindFirstDevice.argtypes = [
        ctypes.POINTER(_BLUETOOTH_DEVICE_SEARCH_PARAMS),
        ctypes.POINTER(_BLUETOOTH_DEVICE_INFO),
    ]
    bth.BluetoothFindFirstDevice.restype = ctypes.c_void_p
    bth.BluetoothFindNextDevice.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_BLUETOOTH_DEVICE_INFO)
    ]
    bth.BluetoothFindNextDevice.restype = ctypes.c_int
    bth.BluetoothFindDeviceClose.argtypes = [ctypes.c_void_p]
    bth.BluetoothFindDeviceClose.restype = ctypes.c_int

    target = int(str(address).replace(":", ""), 16)
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

    h = bth.BluetoothFindFirstDevice(ctypes.byref(search), ctypes.byref(info))
    if not h:
        return False
    try:
        while True:
            if int(info.address) == target:
                return bool(info.fConnected)
            info = _BLUETOOTH_DEVICE_INFO()
            info.dwSize = ctypes.sizeof(_BLUETOOTH_DEVICE_INFO)
            if not bth.BluetoothFindNextDevice(h, ctypes.byref(info)):
                break
    finally:
        bth.BluetoothFindDeviceClose(h)
    return False
