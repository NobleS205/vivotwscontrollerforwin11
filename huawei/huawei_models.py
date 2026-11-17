"""
华为 / HONOR 耳机型号识别与驱动类选择。

镜像 `vivo/vivo_models.py` 的职责。**权威型号名来源**是 vendored OpenFreebuds
的 `DEVICE_TO_DRIVER_MAP`（`openfreebuds/driver/constants.py`），本模块不另立
一份型号表，避免和上游漂移。
"""

from __future__ import annotations

from typing import Optional

from ._vendor import ensure_vendor_on_path

ensure_vendor_on_path()

from openfreebuds.driver.constants import DEVICE_TO_DRIVER_MAP  # noqa: E402

# 设备名里可能带、但注册表 key 也可能带的前缀
_VENDOR_PREFIXES = ("huawei", "honor")


def normalize(value: str) -> str:
    """仅保留字母数字并小写，用于宽松匹配。"""
    return "".join(c for c in (value or "").lower() if c.isalnum())


def _strip_vendor_prefix(norm: str) -> str:
    for p in _VENDOR_PREFIXES:
        if norm.startswith(p):
            return norm[len(p):]
    return norm


# 归一化索引：既支持 "HUAWEI FreeBuds Pro 5"，也支持耳机只上报 "FreeBuds Pro 5"
_NORM_EXACT: dict[str, str] = {}
_NORM_BARE: dict[str, str] = {}
for _key in DEVICE_TO_DRIVER_MAP:
    _n = normalize(_key)
    _NORM_EXACT.setdefault(_n, _key)
    _NORM_BARE.setdefault(_strip_vendor_prefix(_n), _key)


def is_family_name(device_name: Optional[str]) -> bool:
    """是否是华为 / HONOR **耳机**名（注册表命中，或名字含耳机家族关键字）。

    v3.34：去掉了「名字以 huawei / honor 开头就算耳机」这条规则。它把
    `HUAWEI MateBook`、`HONOR MagicBook`、`HUAWEI P60` 这类手机 / 电脑
    也算成耳机，自动识别一看到它们就弹面板（连接又必然失败）。
    现在只认注册表型号与 freebuds / freeclip / freelace / earbuds 关键字。
    """
    if not device_name:
        return False
    n = normalize(device_name)
    if n in _NORM_EXACT or _strip_vendor_prefix(n) in _NORM_BARE:
        return True
    return any(k in n for k in ("freebuds", "freeclip", "freelace", "earbuds"))


def is_supported(device_name: Optional[str]) -> bool:
    """严格判断：型号必须在上游注册表里（能选到驱动才算支持）。

    自动弹窗 / 自动连接只认这个结果。`is_family_name` 只说明「像耳机」，
    例如尚未适配的 `HUAWEI FreeBuds 7` 也像，但不能接管。
    """
    return find_model(device_name) is not None


def find_model(device_name: Optional[str]) -> Optional[str]:
    """返回注册表里的权威型号名；未知返回 None。"""
    if not device_name:
        return None
    n = normalize(device_name)
    if n in _NORM_EXACT:
        return _NORM_EXACT[n]
    bare = _strip_vendor_prefix(n)
    if bare in _NORM_BARE:
        return _NORM_BARE[bare]
    return None


def select_driver_class(device_name: Optional[str]) -> Optional[type]:
    """返回该型号对应的 upstream driver 类；未知返回 None。"""
    model = find_model(device_name)
    if model is None:
        return None
    return DEVICE_TO_DRIVER_MAP.get(model)


def display_name(device_name: Optional[str]) -> str:
    exact = find_model(device_name)
    if exact:
        return exact
    if is_family_name(device_name):
        return "华为 / HONOR 耳机（家族兼容）"
    return device_name or "未知设备"


def supported_models() -> list[str]:
    """注册表全部型号（供 README / 调试用）。"""
    return list(DEVICE_TO_DRIVER_MAP.keys())
