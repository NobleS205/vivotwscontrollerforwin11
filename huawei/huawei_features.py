"""
华为 / HONOR 型号能力矩阵。

镜像 `vivo/vivo_features.py` 的接口（FeatureSet.has / visible / is_experimental
+ resolve(name)），但**能力位不是抄一张表，而是从上游 per_model 驱动里真实的
`self.handlers` 列表推导出来的** —— 这样它不会与 vendor 漂移。

能力位与上游 handler 的对应（每个都注明来源）：
  noise   ← OfbHuaweiAncHandler            (handler_id="anc_global")
  eq      ← OfbHuaweiEqualizerPresetHandler(handler_id="config_eq")
  wear    ← OfbHuaweiStateInEarHandler     (handler_id="tws_in_ear")
  dual    ← OfbHuaweiDualConnectHandler    (handler_id="dual_connect")
  game    ← OfbHuaweiLowLatencyPreferenceHandler (handler_id="low_latency")
  find    ← 上游 **没有**对应 handler ⇒ 任何型号都不给
  spatial ← 上游 **没有**对应 handler ⇒ 任何型号都不给

由此自然得到：
  * FreeBuds Pro 5 (buds_pro_5.OfbDriverHuaweiPro5) 含 OfbHuaweiAncHandler
    → 给 noise。
  * FreeClip 2 (free_clip_2.OfbDriverHuaweiFreeClip2) 开放式耳夹、无传统 ANC，
    其 handlers 里没有 ANC ⇒ 不给 noise（UI 会自动隐藏降噪分段控件）。
  * 两者都没有 find / spatial ⇒ 都不给。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Optional

from ._vendor import ensure_vendor_on_path

ensure_vendor_on_path()

from .huawei_models import find_model, select_driver_class  # noqa: E402

NOISE = "noise"
FIND = "find"
SPATIAL = "spatial"
DUAL = "dual"
GAME = "game"
WEAR = "wear"
EQ = "eq"

# 上游 handler_id → 能力位
_HANDLER_TO_FEATURE: dict[str, str] = {
    "anc_global": NOISE,        # OfbHuaweiAncHandler
    "config_eq": EQ,            # OfbHuaweiEqualizerPresetHandler
    "tws_in_ear": WEAR,         # OfbHuaweiStateInEarHandler
    "dual_connect": DUAL,       # OfbHuaweiDualConnectHandler
    "low_latency": GAME,        # OfbHuaweiLowLatencyPreferenceHandler
}

# 华为侧永远不具备的能力（上游无 handler，且型号间无差异）
_UNSUPPORTED_ON_HUAWEI = frozenset({FIND, SPATIAL})

# 硬件强制常开、无开关的功能：华为侧目前没有
_FORCED: dict[str, FrozenSet[str]] = {}

# 尚未完成全型号真机验证的命令（UI 上加「实验」角标）
EXPERIMENTAL = frozenset({GAME})


@dataclass
class FeatureSet:
    supported: FrozenSet[str]
    forced: FrozenSet[str]

    def has(self, feature: str) -> bool:
        return feature in self.supported

    def visible(self, feature: str) -> bool:
        """有能力且非强制常开，才显示可操作控件。"""
        return feature in self.supported and feature not in self.forced

    def is_experimental(self, feature: str) -> bool:
        return feature in self.supported and feature in EXPERIMENTAL


def _features_from_handlers(driver_cls: type, model: str) -> FrozenSet[str]:
    """实例化驱动、读取其 handlers，推导能力位（只读、无 I/O）。"""
    try:
        drv = driver_cls("00:00:00:00:00:00")
    except Exception:
        return frozenset()
    out: set[str] = set()
    for handler in getattr(drv, "handlers", []) or []:
        hid = getattr(handler, "handler_id", None)
        if hid in _HANDLER_TO_FEATURE:
            out.add(_HANDLER_TO_FEATURE[hid])
    out -= _UNSUPPORTED_ON_HUAWEI
    return frozenset(out)


_UNKNOWN = "未知"


def resolve(device_name: Optional[str]) -> FeatureSet:
    """
    按型号解析能力集。

    未知型号：保守处理（**不乐观全开**），只给两端共有的基础位，
    避免对不支持的设备展示无效控件。
    """
    model = find_model(device_name)
    if model is None:
        # 保守：连型号都认不出时不宣称任何高级能力
        return FeatureSet(supported=frozenset(), forced=frozenset())

    driver_cls = select_driver_class(model)
    if driver_cls is None:
        return FeatureSet(supported=frozenset(), forced=frozenset())

    supported = _features_from_handlers(driver_cls, model)
    forced = _FORCED.get(model, frozenset())
    return FeatureSet(supported=supported, forced=forced)
