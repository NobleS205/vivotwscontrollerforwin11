"""
OPPO / 一加 / realme 耳机型号识别（**只负责名字，不负责能力**）。

为什么要单独一张表：
  * 蓝牙名可能是 `OPPO Enco X4`、`OnePlus Buds Pro 3`、`realme Buds Air 6 Pro`，
    需要统一成规范名显示在标题栏；
  * 自动识别 / 手动列表要能把手机、手表、笔记本挡在外面（`OPPO Find X8`、
    `OnePlus 13`、`realme GT7` 都不是耳机）。

**能力不写死在这张表里**：OPPO 的 ANC / 空间音频是否可用，连接后由
`0x010C`（降噪模式）与 `0x012A`（空间音频）的实际回包决定（见
`oppo/controller.py` 的能力发现）。型号表只解决「认不认识这个名字」。

型号清单参考 2026 年官方在售型号，覆盖 OPPO Enco / 一加 Buds / realme Buds
三条产品线；未列出的新型号只要名字属于这三个家族，仍会按家族放行。
"""

from __future__ import annotations

import re
from typing import Dict, Optional

#: 规范名（按产品线分组；仅用于显示与日志）
MODELS = (
    # ---- OPPO Enco ----
    "OPPO Enco X4", "OPPO Enco X3", "OPPO Enco X2", "OPPO Enco X",
    "OPPO Enco Free4", "OPPO Enco Free3", "OPPO Enco Free2", "OPPO Enco Free2i",
    "OPPO Enco Free",
    "OPPO Enco Air5 Pro", "OPPO Enco Air5s", "OPPO Enco Air5",
    "OPPO Enco Air4 Pro", "OPPO Enco Air4", "OPPO Enco Air3 Pro", "OPPO Enco Air3",
    "OPPO Enco Air2 Pro", "OPPO Enco Air2s", "OPPO Enco Air2", "OPPO Enco Air",
    "OPPO Enco Buds3 Pro", "OPPO Enco Buds3", "OPPO Enco Buds2", "OPPO Enco Buds",
    "OPPO Enco R3", "OPPO Enco R2",
    "OPPO Enco W51", "OPPO Enco W31",
    "OPPO Enco M32", "OPPO Enco M31",
    # ---- 一加 ----
    "OnePlus Buds Pro 3", "OnePlus Buds Pro 2", "OnePlus Buds Pro",
    "OnePlus Buds 4", "OnePlus Buds 3", "OnePlus Buds",
    "OnePlus Nord Buds 3 Pro", "OnePlus Nord Buds 3", "OnePlus Nord Buds 2r",
    "OnePlus Nord Buds 2", "OnePlus Nord Buds", "OnePlus Buds N",
    "OnePlus Buds Z2", "OnePlus Buds Z",
    # ---- realme ----
    "realme Buds Air 7 Pro", "realme Buds Air 6 Pro", "realme Buds Air 6",
    "realme Buds Air 5 Pro", "realme Buds Air 5", "realme Buds Air 4 Pro",
    "realme Buds Air 4", "realme Buds Air 3 Pro", "realme Buds Air 3",
    "realme Buds Air 2 Pro", "realme Buds Air 2", "realme Buds Air",
    "realme Buds T300", "realme Buds T110", "realme Buds T100",
    "realme Buds Q2s",
)

#: 家族前缀（小写）。命中即认为「这是一只 OPPO 系耳机」。
FAMILY_PREFIXES = (
    "oppo enco",
    "oneplus buds",
    "oneplus nord buds",
    "realme buds",
)

#: 归一化时去掉的厂商前缀（便于 `Enco X4` 这种简写也能匹配）
_VENDOR_PREFIXES = ("oppo ", "oneplus ", "realme ")

#: 型号名后面常见的固件 / 位置后缀
_SUFFIX_RE = re.compile(
    r"\s*(?:\([^)]*\)|\[[^\]]*\]|（[^）]*）|"
    r"\b(?:l|r|left|right|le|ble|audio)\b)\s*$",
    re.IGNORECASE,
)

_NORM_TO_CANON: Dict[str, str] = {m.lower(): m for m in MODELS}


def normalize(name: Optional[str]) -> str:
    n = (name or "").strip().lower().replace("_", " ")
    n = re.sub(r"\s+", " ", n)
    return n


def _strip_vendor(name: str) -> str:
    for p in _VENDOR_PREFIXES:
        if name.startswith(p):
            return name[len(p):]
    return name


def _strip_suffix(name: str) -> str:
    prev = None
    while prev != name:
        prev = name
        name = _SUFFIX_RE.sub("", name).strip()
    return name


def find_model(device_name: Optional[str]) -> Optional[str]:
    """精确 / 去后缀匹配型号表；认不出返回 None。"""
    n = normalize(device_name)
    if not n:
        return None
    candidates = {n, _strip_suffix(n)}
    bare = _strip_vendor(n)
    candidates.add(bare)
    candidates.add(_strip_suffix(bare))
    for c in candidates:
        hit = _NORM_TO_CANON.get(c)
        if hit:
            return hit
    # 不做「前缀模糊匹配」：`OPPO Enco X4 Ultra` 是另一款型号，
    # 硬套成 X4 会显示错误的型号名；这类新型号交给家族兜底（协议相同）。
    return None


def is_family_name(device_name: Optional[str]) -> bool:
    n = normalize(device_name)
    if not n:
        return False
    return any(n.startswith(p) for p in FAMILY_PREFIXES)


def display_name(device_name: Optional[str]) -> str:
    exact = find_model(device_name)
    if exact:
        return exact
    if is_family_name(device_name):
        return "OPPO / 一加 / realme 耳机（家族兼容）"
    return device_name or "未知设备"


def brand_of(device_name: Optional[str]) -> str:
    """产品线标签（仅用于日志与标题，不参与能力判断）。"""
    n = normalize(device_name)
    if n.startswith("oneplus"):
        return "OnePlus"
    if n.startswith("realme"):
        return "realme"
    return "OPPO"
