"""
OPPO 耳机能力集合。

与 vivo / 华为不同，OPPO 不提供「按型号查能力」的公开表，靠型号名猜
（例如 X4 有空间音频、Air3 没有降噪）很容易猜错。因此这里只保留
**连接后能够确认**的能力：

  * 基础能力（EQ / 佩戴 / 查找）——协议命令全系通用，直接给；
  * 降噪 —— 收到 `0x810C` 且上报的模式不是「仅关闭」才给；
  * 空间音频 —— 收到 `0x812A` 或功能开关 27 才给；
  * 游戏模式 —— 功能开关 6 有回读才给。

控制器在收到对应回包时调用 `grant()` 扩容，UI 会随即补出对应分区。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Iterable

NOISE = "noise"
FIND = "find"
SPATIAL = "spatial"
GAME = "game"
WEAR = "wear"
EQ = "eq"
DUAL = "dual"

#: 连上就有的基础能力（不需要额外探测）
BASE = frozenset({EQ, WEAR, FIND})

#: 命令仍在真机上验证中的功能（UI 上加「实验」角标）
EXPERIMENTAL = frozenset({SPATIAL, GAME})


@dataclass(frozen=True)
class FeatureSet:
    supported: FrozenSet[str]
    forced: FrozenSet[str] = frozenset()

    def has(self, feature: str) -> bool:
        return feature in self.supported

    def visible(self, feature: str) -> bool:
        return feature in self.supported and feature not in self.forced

    def is_experimental(self, feature: str) -> bool:
        return feature in self.supported and feature in EXPERIMENTAL


def base() -> FeatureSet:
    return FeatureSet(BASE)


def with_extra(caps: Iterable[str]) -> FeatureSet:
    return FeatureSet(BASE | frozenset(caps))
