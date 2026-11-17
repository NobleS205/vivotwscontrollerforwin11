"""
OPPO 演示传输：内存回环模拟一只 Enco X4，用于没有真机 / 非 Windows 时
跑通「连接 → 握手 → 电量 → 降噪 → 空间音频 → EQ」整条链路。

响应全部按 `oppo_protocol` 的真实帧格式生成，因此它同时也是协议编码 /
解码的活体单元测试。
"""

from __future__ import annotations

import queue
import threading
from typing import Optional

from . import oppo_protocol as op
from .transport import Transport


class DemoTransport(Transport):
    def __init__(self, name: str = "OPPO Enco X4（演示）") -> None:
        self.device_name = name
        self._rx: "queue.Queue[bytes]" = queue.Queue()
        self._decoder = op.Decoder()
        self._connected = False
        self._lock = threading.Lock()
        # 模拟状态
        self._noise = op.NOISE_ANC
        self._spatial = 0
        self._game = False
        self._eq = 0
        self._battery = (78, 76, 64)
        self._switches = {
            op.SWITCH_WEAR_DETECT: 1,
            op.SWITCH_GAME: 0,
            op.SWITCH_DUAL: 1,
            op.SWITCH_SPATIAL_V1: 0,
            op.SWITCH_HEAD_TRACK: 0,
        }

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        self._connected = True
        # 真机连上后会主动索要一次主机时间
        self._rx.put(op.frame(0x0500, 0x00))

    def close(self) -> None:
        self._connected = False

    def send(self, data: bytes) -> None:
        for fr in self._decoder.offer(data):
            self._handle(fr)

    def recv(self, timeout: float = 0.5) -> bytes:
        try:
            return self._rx.get(timeout=timeout)
        except queue.Empty:
            return b""

    # ------------------------------------------------------------------ #
    def _reply(self, cmd: int, seq: int, payload: bytes) -> None:
        self._rx.put(op.frame(cmd | 0x8000, seq, payload))

    def _push(self, cmd: int, payload: bytes) -> None:
        self._rx.put(op.frame(cmd, 0x80, payload))

    def _handle(self, fr: op.Frame) -> None:
        cmd = fr.cmd
        if cmd == op.CMD_HANDSHAKE:
            self._reply(cmd, fr.seq, b"\x00" + bytes([
                0xFF, 0x77, 0x5A, 0xEE, 0xEE, 0x8F, 0xA6, 0x5F, 0x17, 0x00]))
        elif cmd == op.CMD_PRODUCT_ID:
            self._reply(cmd, fr.seq, b"\x00\x10\xEC\x06")
        elif cmd == op.CMD_EVENTS_GET:
            self._reply(cmd, fr.seq, bytes([0x00, 0x09]) + bytes(
                [0x01, 0x02, 0x03, 0x04, 0x08, 0x0B, 0xF1, 0xF2, 0xF3]))
        elif cmd == op.CMD_EVENTS_SUB:
            self._reply(cmd, fr.seq, b"\x00")
        elif cmd == op.CMD_FIRMWARE:
            csv = b"1,2,116,2,2,116,3,2,100"
            self._reply(cmd, fr.seq, bytes([0x00, len(csv)]) + csv)
        elif cmd == op.CMD_BATTERY:
            left, right, case = self._battery
            self._reply(cmd, fr.seq, bytes([
                0x00, 0x03,
                0x01, 0x80 | left,
                0x02, 0x80 | right,
                0x03, case,
            ]))
        elif cmd == op.CMD_WEAR:
            self._reply(cmd, fr.seq, bytes([0x00, 0x02, 0x01, 0x07, 0x02, 0x07]))
        elif cmd == op.CMD_NOISE_GET:
            self._reply(cmd, fr.seq, bytes([
                0x00, 0x01, 0x01, 1 << self._noise]))
        elif cmd == op.CMD_NOISE_SET:
            mask = int.from_bytes(fr.payload[2:], "little") \
                if len(fr.payload) > 2 else 0
            if mask:
                self._noise = (mask & -mask).bit_length() - 1
            self._reply(cmd, fr.seq, b"\x00")
            self._push(0x810C, bytes([
                0x00, 0x01, 0x01, 1 << self._noise]))
        elif cmd == op.CMD_SPATIAL_GET:
            self._reply(cmd, fr.seq, bytes([0x00, self._spatial]))
        elif cmd == op.CMD_SPATIAL_SET:
            self._spatial = fr.payload[0] if fr.payload else 0
            self._reply(cmd, fr.seq, b"\x00")
            self._push(0x812A, bytes([0x00, self._spatial]))
        elif cmd == op.CMD_SWITCH_GET:
            ids = list(fr.payload[1:]) if len(fr.payload) > 1 else []
            items = [(i, self._switches.get(i, 0)) for i in ids]
            body = bytes([0x00, len(items)])
            for i, v in items:
                body += bytes([i, v])
            self._reply(cmd, fr.seq, body)
        elif cmd == op.CMD_SWITCH_SET:
            if len(fr.payload) >= 2:
                self._switches[fr.payload[0]] = fr.payload[1]
                if fr.payload[0] == op.SWITCH_GAME:
                    self._game = bool(fr.payload[1])
            self._reply(cmd, fr.seq, b"\x00")
        elif cmd == op.CMD_EQ_GET:
            self._reply(cmd, fr.seq, bytes([0x00, self._eq]))
        elif cmd == op.CMD_EQ_SET:
            if fr.payload:
                self._eq = fr.payload[0]
            self._reply(cmd, fr.seq, b"\x00")
        elif cmd == op.CMD_FIND:
            self._reply(cmd, fr.seq, b"\x00")
        elif cmd == op.CMD_HOST_TIME:
            pass  # 主机应答时间，无需回复
        else:
            # 未知命令统一回「不支持」，避免主机无限等
            self._reply(cmd, fr.seq, b"\x0F")
