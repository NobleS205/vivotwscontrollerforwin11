"""
OPPO / 一加 / realme 耳机控制协议（OPO v1，经典蓝牙 RFCOMM）。

帧格式与命令来自社区逆向，参考：
  * TIANLI0/EncoDeck `docs/PROTOCOL_FINDINGS.md`（GPL-3.0，Enco X4 实测）
  * Swastik36/OPPO-Earbuds `docs/PROTOCOL.md`（GPL-3.0，Enco Buds3 Pro 实测）
  * Zhaoyi-ya/OppoPodsManager（GPL-3.0，135 款型号表）

链路层：
    AA | LEN(变长 base-128) | CTRL | 00 | [FSN] | 应用层数据
应用层：
    CMD(u16 LE) | SEQ(u8) | PLEN(u16 LE) | payload

注意与 vivo GAIA / 华为 0x5A 完全不同：OPPO 无校验、无加密，
响应 CMD = 请求 CMD | 0x8000，并回显请求的 SEQ。
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from typing import List, Optional

FRAME_START = 0xAA

# ---- 查询组 0x01xx ----
CMD_HANDSHAKE = 0x0100
CMD_PRODUCT_ID = 0x0103
CMD_FIRMWARE = 0x0105
CMD_BATTERY = 0x0106
CMD_WEAR = 0x0109
CMD_NOISE_GET = 0x010C
CMD_SWITCH_GET = 0x010D
CMD_EQ_GET = 0x010F
CMD_SPATIAL_GET = 0x012A

# ---- 设置组 0x04xx ----
CMD_FIND = 0x0400
CMD_SWITCH_SET = 0x0403
CMD_NOISE_SET = 0x0404
CMD_EQ_SET = 0x0406
CMD_SPATIAL_SET = 0x0422

# ---- 事件订阅 ----
CMD_EVENTS_GET = 0x0200
CMD_EVENTS_SUB = 0x0205
CMD_HOST_TIME = 0x8500

#: 订阅电量 / 佩戴 / 降噪三类推送（其余事件需要主机回复，不订阅）
SUBSCRIBE_EVENTS = (0x01, 0x02, 0x03)

#: OPPO 的降噪 protocolIndex（型号配置里的模式树）
NOISE_OFF = 0
NOISE_ANC = 1
NOISE_TRANSPARENCY = 2

#: 功能开关 ID
SWITCH_WEAR_DETECT = 4
SWITCH_GAME = 6
SWITCH_DUAL = 17
SWITCH_SPATIAL_V1 = 27
SWITCH_HEAD_TRACK = 59

#: 写入/查询失败的状态码 → 人话
STATUS_TEXT = {
    0x00: "成功",
    0x09: "耳机忙碌或被其它设备占用",
    0x0E: "需要先戴上耳机",
    0x0F: "耳机忙碌",
    0x10: "耳机电量过低",
}

#: OPPO protocolIndex → UI 三档（0 降噪 / 1 关闭 / 2 通透）
#: 4/5/6 是降噪下的深度/中度/轻度，7 智能，11 自适应，12/13/14 自适应子档
_INDEX_TO_UI = {
    0: 1, 3: 1,
    1: 0, 4: 0, 5: 0, 6: 0, 7: 0, 11: 0, 12: 0, 13: 0, 14: 0,
    2: 2, 8: 2,
}

#: UI 三档 → 要下发的 protocolIndex（用顶层节点，让耳机自己挑档位）
_UI_TO_INDEX = {0: NOISE_ANC, 1: NOISE_OFF, 2: NOISE_TRANSPARENCY}


@dataclass
class Frame:
    cmd: int
    seq: int
    payload: bytes
    status: int = -1          # payload 首字节（响应帧的状态码）
    fragmented: bool = False

    @property
    def response_of(self) -> Optional[int]:
        """若是响应帧，返回对应的请求 CMD。"""
        if self.cmd & 0x8000:
            return self.cmd & 0x7FFF
        return None


@dataclass
class Battery:
    left: Optional[int] = None
    left_charging: bool = False
    right: Optional[int] = None
    right_charging: bool = False
    case: Optional[int] = None
    case_charging: bool = False


# --------------------------------------------------------------------------- #
# 编解码
# --------------------------------------------------------------------------- #
def encode_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _decode_varint(buf: bytes, pos: int):
    value = 0
    shift = 0
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, pos
        shift += 7
        if shift > 21:
            return None, pos
    return None, pos


def frame(cmd: int, seq: int = 0, payload: bytes = b"") -> bytes:
    """组一条完整帧（单帧，CTRL=0，无校验）。"""
    app = struct.pack("<H", cmd & 0xFFFF) + bytes([seq & 0xFF]) \
        + struct.pack("<H", len(payload)) + payload
    body = b"\x00\x00" + app
    return bytes([FRAME_START]) + encode_varint(len(body)) + body


class Decoder:
    """半包 / 粘包 / 非法头重同步。"""

    def __init__(self) -> None:
        self._buf = bytearray()

    def reset(self) -> None:
        self._buf.clear()

    def offer(self, chunk: bytes) -> List[Frame]:
        if chunk:
            self._buf.extend(chunk)
        out: List[Frame] = []
        while True:
            buf = self._buf
            start = buf.find(FRAME_START)
            if start < 0:
                buf.clear()
                break
            if start > 0:
                del buf[:start]
            if len(buf) < 2:
                break
            length, body_pos = _decode_varint(bytes(buf), 1)
            if length is None or length <= 0 or length > 4096:
                del buf[0]
                continue
            if len(buf) < body_pos + length:
                break
            body = bytes(buf[body_pos:body_pos + length])
            del buf[:body_pos + length]
            fr = _parse_body(body)
            if fr is not None:
                out.append(fr)
        return out


def _parse_body(body: bytes) -> Optional[Frame]:
    if len(body) < 2:
        return None
    ctrl = body[0]
    if ctrl & 0x03:
        # 分片帧：我们只用单帧命令（电量/降噪/空间音频都很短），
        # 遇到分片先丢弃，避免把 FSN 当成 CMD 解析错位。
        return None
    app = body[2:]
    if len(app) < 5:
        return None
    cmd, seq, plen = struct.unpack("<HBH", app[:5])
    payload = app[5:5 + plen]
    status = payload[0] if payload else -1
    return Frame(cmd=cmd, seq=seq, payload=payload, status=status,
                 fragmented=False)


# --------------------------------------------------------------------------- #
# 命令构造
# --------------------------------------------------------------------------- #
def cmd_handshake() -> bytes:
    return frame(CMD_HANDSHAKE, 0x00)


def cmd_product_id() -> bytes:
    return frame(CMD_PRODUCT_ID, 0x01)


def cmd_query_events() -> bytes:
    return frame(CMD_EVENTS_GET, 0x00)


def cmd_subscribe(events=SUBSCRIBE_EVENTS) -> bytes:
    return frame(CMD_EVENTS_SUB, 0x00, bytes([len(events), *events]))


def cmd_firmware() -> bytes:
    return frame(CMD_FIRMWARE, 0x02)


def cmd_battery() -> bytes:
    return frame(CMD_BATTERY, 0xF0)


def cmd_wear() -> bytes:
    return frame(CMD_WEAR, 0x03)


def cmd_noise_get() -> bytes:
    # [子类型 1 = 当前模式][长度 1]
    return frame(CMD_NOISE_GET, 0x04, bytes([0x01, 0x01]))


def cmd_noise_set(ui_mode: int) -> bytes:
    index = _UI_TO_INDEX.get(int(ui_mode), NOISE_ANC)
    mask_len = index // 8 + 1
    mask = (1 << index).to_bytes(mask_len, "little")
    # [01][01][掩码…]
    return frame(CMD_NOISE_SET, 0x05, bytes([0x01, 0x01]) + mask)


def cmd_spatial_get() -> bytes:
    return frame(CMD_SPATIAL_GET, 0x06)


def cmd_spatial_set(mode: int) -> bytes:
    return frame(CMD_SPATIAL_SET, 0x07, bytes([int(mode) & 0x03]))


def cmd_eq_get() -> bytes:
    return frame(CMD_EQ_GET, 0x08)


def cmd_eq_set(code: int) -> bytes:
    return frame(CMD_EQ_SET, 0x09, bytes([int(code) & 0xFF]))


def cmd_switch_get(ids=(SWITCH_WEAR_DETECT, SWITCH_GAME, SWITCH_DUAL,
                        SWITCH_SPATIAL_V1, SWITCH_HEAD_TRACK)) -> bytes:
    return frame(CMD_SWITCH_GET, 0x0A, bytes([len(ids), *ids]))


def cmd_switch_set(switch_id: int, on: bool) -> bytes:
    return frame(CMD_SWITCH_SET, 0x0B, bytes([switch_id & 0xFF, 1 if on else 0]))


def cmd_find(start: bool) -> bytes:
    return frame(CMD_FIND, 0x0C, bytes([1 if start else 0]))


def cmd_host_time(now: Optional[float] = None) -> bytes:
    ts = int(now if now is not None else time.time())
    return frame(CMD_HOST_TIME, 0x00, b"\x00" + struct.pack("<I", ts & 0xFFFFFFFF))


def initial_commands() -> List[bytes]:
    """连上后的初始化序列（顺序与 EncoDeck 在 X4 上验证的一致）。"""
    return [
        cmd_handshake(),
        cmd_product_id(),
        cmd_query_events(),
        cmd_subscribe(),
        cmd_firmware(),
        cmd_battery(),
        cmd_wear(),
        cmd_noise_get(),
        cmd_eq_get(),
        cmd_spatial_get(),
        cmd_switch_get(),
    ]


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def _scan_pairs(payload: bytes):
    """在 payload 里找 `[n]{a,b}×n` 结构，容忍前面 1~3 字节的状态/子类型。"""
    for off in range(0, min(4, len(payload))):
        n = payload[off]
        if n <= 0 or n > 8:
            continue
        if off + 1 + 2 * n > len(payload):
            continue
        pairs = []
        ok = True
        for k in range(n):
            a = payload[off + 1 + 2 * k]
            b = payload[off + 2 + 2 * k]
            if a not in (1, 2, 3):
                ok = False
                break
            pairs.append((a, b))
        if ok:
            return pairs
    return []


def parse_battery(payload: bytes) -> Optional[Battery]:
    pairs = _scan_pairs(payload)
    if not pairs:
        return None
    b = Battery()
    for comp, raw in pairs:
        pct = raw & 0x7F
        charging = bool(raw & 0x80)
        if pct > 100:
            pct = 100
        if comp == 1:
            b.left, b.left_charging = pct, charging
        elif comp == 2:
            b.right, b.right_charging = pct, charging
        elif comp == 3:
            b.case, b.case_charging = pct, charging
    return b


def parse_wear(payload: bytes):
    """返回 (left, right) 文本（worn / in_case / removed / unknown）。

    OPPO 的佩戴标志 bit0=已出盒、bit1=入耳、bit2=0 盒盖关闭。
    文档里的示例同时出现过「计数 + 单个标志」和「组件 + 标志」两种长度，
    这里两种都兼容，避免因固件差异整块丢弃。
    """
    if not payload:
        return None
    body = payload[1:] if payload[0] == 0 else payload
    if not body:
        return None
    pairs = _scan_pairs(payload)
    states = {}
    if pairs:
        for comp, flags in pairs:
            states[comp] = _flags_to_state(flags)
    elif len(body) >= 2:
        flags = body[-1]
        st = _flags_to_state(flags)
        states[1] = states[2] = st
    elif len(body) == 1:
        st = _flags_to_state(body[0])
        states[1] = states[2] = st
    if not states:
        return None
    return (states.get(1, "unknown"), states.get(2, "unknown"))


def _flags_to_state(flags: int) -> str:
    if flags & 0x02:
        return "worn"
    if not flags & 0x01:
        return "in_case"
    return "removed"


def parse_noise(payload: bytes) -> Optional[int]:
    """响应 `[00][子类型][类型][掩码…]` → UI 三档（0 降噪 / 1 关闭 / 2 通透）。"""
    if len(payload) < 3:
        return None
    if payload[0] != 0:
        return None
    mask = payload[3:]
    if not mask:
        return None
    value = int.from_bytes(mask, "little")
    if value == 0:
        return None
    # 优先看降噪 / 通透这两个顶层位：只有关闭位时不算「支持降噪」
    if value & (1 << NOISE_ANC):
        return 0
    if value & (1 << NOISE_TRANSPARENCY):
        return 2
    index = (value & -value).bit_length() - 1
    return _INDEX_TO_UI.get(index, 1)


def parse_noise_index(payload: bytes) -> Optional[int]:
    """返回耳机上报的原始 protocolIndex（用于判断有无 ANC）。"""
    if len(payload) < 4 or payload[0] != 0:
        return None
    value = int.from_bytes(payload[3:], "little")
    if value == 0:
        return None
    return (value & -value).bit_length() - 1


def parse_spatial(payload: bytes) -> Optional[int]:
    """响应 `[00][模式]`，0 关 / 1 固定 / 2 头部跟踪。"""
    if len(payload) < 2 or payload[0] != 0:
        return None
    return payload[1]


def parse_eq(payload: bytes) -> Optional[int]:
    if len(payload) < 2 or payload[0] != 0:
        return None
    return payload[1]


def parse_switch_values(payload: bytes) -> Optional[dict]:
    """响应 `[00][n]{id, 值}×n` → {id: value}。"""
    pairs = _scan_pairs(payload)
    # 开关的「组件」是功能 ID，不限于 1~3，_scan_pairs 会把它们过滤掉，
    # 因此这里单独解析：状态(1) + 数量(1) + 每项 2 字节。
    out = {}
    if len(payload) >= 2:
        n = payload[1]
        if 0 < n <= 16 and 2 + 2 * n <= len(payload):
            for i in range(n):
                out[payload[2 + 2 * i]] = payload[3 + 2 * i]
            return out
    if pairs:  # 兜底（有些事件帧没有数量字节）
        return {a: b for a, b in pairs}
    return out or None


def parse_firmware(payload: bytes) -> Optional[str]:
    """响应 `[00][n] "组件,类型,版本,…"` → "116 / 116 / 100" 风格。"""
    if len(payload) < 3 or payload[0] != 0:
        return None
    n = payload[1]
    raw = payload[2:2 + n] if 0 < n <= len(payload) - 2 else payload[2:]
    try:
        text = raw.decode("utf-8", "replace").strip("\x00 ")
    except Exception:
        return None
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        return None
    versions = []
    for i in range(0, len(parts) - 2, 3):
        versions.append(parts[i + 2])
    if not versions:
        return text
    if len(set(versions)) == 1:
        return versions[0]
    return " / ".join(versions)


def is_success(fr: Frame) -> bool:
    return fr.status == 0x00


def status_text(code: int) -> str:
    return STATUS_TEXT.get(code, f"耳机返回状态 0x{code:02X}")
