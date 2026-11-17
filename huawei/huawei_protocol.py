"""
华为 / HONOR 耳机协议层：向 UI 暴露稳定的常量与编解码入口。

本模块 **不做任何协议编解码实现** —— 帧格式、CRC16、TLV 全部复用
vendored 的 OpenFreebuds 源码（`openfreebuds.driver.huawei.package` /
`utils` / `constants.spp_commands`），这里只做「再导出 + 语义映射」一层薄封装。

对应 vivo 版的 `vivo/vivo_protocol.py`，能力位命名与 UI 契约保持一致。
"""

from __future__ import annotations

from enum import IntEnum

from ._vendor import ensure_vendor_on_path

ensure_vendor_on_path()

# --- 复用上游编解码（不重写） --------------------------------------------- #
from openfreebuds.driver.huawei.package import HuaweiSppPackage  # noqa: E402
from openfreebuds.driver.huawei.utils import crc16_xmodem  # noqa: E402
from openfreebuds.driver.huawei.constants import (  # noqa: E402
    CMD_BATTERY_READ,
    CMD_BATTERY_NOTIFY,
    CMD_LOW_LATENCY,
    CMD_DUAL_CONNECT_ENABLED_READ,
    CMD_DUAL_CONNECT_ENABLED_WRITE,
    CMD_DUAL_CONNECT_ENUMERATE,
    CMD_DUAL_CONNECT_PREFERRED_WRITE,
    CMD_DUAL_CONNECT_EXECUTE,
    CMD_DUAL_CONNECT_CHANGE_EVENT,
)

__all__ = [
    "NoiseMode",
    "AUDIO_EFFECT_CODES",
    "MC_DISCONNECTED",
    "MC_CONNECTED_INACTIVE",
    "MC_CONNECTED_ACTIVE",
    "ANC_MODES",
    "PAIRING_SERVICE_UUID",
    "SPP_CHANNEL",
    "HuaweiSppPackage",
    "crc16_xmodem",
    "NoiseModeToVendor",
    "VendorToNoiseMode",
]


class NoiseMode(IntEnum):
    """UI 三档分段控件使用的降噪语义（0/1/2 必须与 vivo 版一致）。"""

    ANC = 0
    OFF = 1
    TRANSPARENCY = 2


# UI 分段值 ↔ 上游 ANC handler 的 mode 字符串
# 上游 OfbHuaweiAncHandler.mode_options = {0: "normal", 1: "cancellation", 2: "awareness"}
#   → 语义：0=关（normal）/ 1=降噪（cancellation）/ 2=通透（awareness）
NoiseModeToVendor = {
    NoiseMode.ANC: "cancellation",
    NoiseMode.OFF: "normal",
    NoiseMode.TRANSPARENCY: "awareness",
}
VendorToNoiseMode = {v: k for k, v in NoiseModeToVendor.items()}
ANC_MODES = tuple(NoiseMode)

# --- 音效（EQ）预设 -------------------------------------------------------- #
# 华为侧各型号的内置预设编号不同：
#   FreeBuds Pro 5 : {5: default, 1: hardbass, 2: treble, 9: voice}
#   FreeClip 2     : {1: default, 2: hardbass, 3: treble, 9: voice}
# UI 需要使用「连续编号」作为 chip 的 key，因此这里用 0..3 作为 UI 序号，
# 具体到设备的物理 preset id 由 `equalizer_preset_id(model, ui_code)` 映射。
AUDIO_EFFECT_CODES: dict[int, str] = {
    0: "默认",
    1: "超重低音",
    2: "清亮高音",
    3: "清澈人声",
}

# 型号 → (UI 序号 → 上游 preset id)
# 数据来源：per_model/buds_pro_5.py 与 per_model/free_clip_2.py 的 w_presets
_EQ_PRESET_IDS: dict[str, dict[int, int]] = {
    "HUAWEI FreeBuds Pro 5": {0: 5, 1: 1, 2: 2, 3: 9},
    "HUAWEI FreeClip 2": {0: 1, 1: 2, 2: 3, 3: 9},
}
_EQ_PRESET_IDS_DEFAULT = {0: 1, 1: 2, 2: 3, 3: 9}

# UI 序号 → 上游 preset 逻辑名（与物理 id 无关，跨型号稳定）
_UI_TO_LOGICAL = {
    0: "equalizer_preset_default",
    1: "equalizer_preset_hardbass",
    2: "equalizer_preset_treble",
    3: "equalizer_preset_voices",
}
_LOGICAL_TO_UI = {v: k for k, v in _UI_TO_LOGICAL.items()}


def equalizer_preset_id(model: str | None, ui_code: int) -> int | None:
    """把 UI 的连续编号映射到指定型号的物理 preset id。"""
    table = _EQ_PRESET_IDS.get(model or "", _EQ_PRESET_IDS_DEFAULT)
    return table.get(ui_code)


def ui_code_from_preset_id(model: str | None, preset_id: int) -> int | None:
    """把耳机上报的物理 preset id 反查回 UI 连续编号。

    ⚠️ 物理 id → 语义是**按型号**的：同一个 id=1 在 FreeBuds Pro 5 上是
    「超重低音」，在 FreeClip 2 上是「默认」。因此必须按该型号的
    `_EQ_PRESET_IDS` 表反查，不能查全局 id→名字表（v3.16 之前那张表把
    2/3 都写成 treble，会让 FreeClip 2 选错 chip）。
    """
    table = _EQ_PRESET_IDS.get(model or "", _EQ_PRESET_IDS_DEFAULT)
    for ui_code, pid in table.items():
        if pid == preset_id:
            return ui_code
    return None


def preset_logical_name_for(model: str | None, ui_code: int) -> str | None:
    """UI 连续编号 → 上游 set_property 需要的逻辑名（按型号校验存在性）。"""
    if equalizer_preset_id(model, ui_code) is None:
        return None
    return _UI_TO_LOGICAL.get(ui_code)


def ui_code_from_logical_name(model: str | None, logical: str) -> int | None:
    """上游上报的逻辑名 → UI 连续编号（型号不认识该预设时返回 None）。"""
    ui_code = _LOGICAL_TO_UI.get(str(logical))
    if ui_code is None:
        return None
    if equalizer_preset_id(model, ui_code) is None:
        return None
    return ui_code


# --- 多设备（双连）状态 ---------------------------------------------------- #
MC_DISCONNECTED = 0
MC_CONNECTED_INACTIVE = 1
MC_CONNECTED_ACTIVE = 2

# 华为走 SPP/RFCOMM，channel 固定为 1（per_model 驱动里 `_spp_service_port = 1`）
SPP_CHANNEL = 1
PAIRING_SERVICE_UUID = "00001101-0000-1000-8000-00805f9b34fb"  # 标准 SPP（仅参考）


# 命令号再导出，方便上层引用（值本身来自 vendor）
__all__ += [
    "CMD_BATTERY_READ",
    "CMD_BATTERY_NOTIFY",
    "CMD_LOW_LATENCY",
    "CMD_DUAL_CONNECT_ENABLED_READ",
    "CMD_DUAL_CONNECT_ENABLED_WRITE",
    "CMD_DUAL_CONNECT_ENUMERATE",
    "CMD_DUAL_CONNECT_PREFERRED_WRITE",
    "CMD_DUAL_CONNECT_EXECUTE",
    "CMD_DUAL_CONNECT_CHANGE_EVENT",
    "equalizer_preset_id",
    "ui_code_from_preset_id",
    "preset_logical_name_for",
    "ui_code_from_logical_name",
    "eq_options",
]


def eq_options(model: str | None) -> list[tuple[int, str]]:
    """该型号可用的 UI 音效选项 [(UI 序号, 中文名), ...]（按序号升序）。"""
    table = _EQ_PRESET_IDS.get(model or "", _EQ_PRESET_IDS_DEFAULT)
    return [(code, AUDIO_EFFECT_CODES[code])
            for code in sorted(table) if code in AUDIO_EFFECT_CODES]
