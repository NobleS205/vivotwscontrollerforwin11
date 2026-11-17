"""
FreeClip 2 佩戴状态补丁（集成层，**不改 vendor 源码**）。

问题现象
--------
连上 HUAWEI FreeClip 2 后，界面一直显示「未佩戴」，即使耳机正戴在耳朵上。

根因
----
上游 `OfbHuaweiStateInEarHandler` 只监听命令 **0x2B03**，而且 `on_init` 里
直接 `put_property("state", "in_ear", "false")`（不查询、先写死 false）。
FreeClip 2 的佩戴通知走的是另一条命令 **0x2B25**（参数 1/2 分别是左右耳
的佩戴标志，两只都在耳才算 `in_ear=true`），于是：
  1. 初始化写入 false；
  2. 真正的佩戴通知 2B25 没有 handler 认领（驱动日志里是
     "Got unsupported package"）；
  3. in_ear 永远停在 false → 面板永远「未佩戴」。

依据（实测来源，不是猜测）
------------------------
OpenFreebuds PR #144（作者 YanamiLab，在 M4 Pro + FreeClip 2 上实测）：
「Handle `2B25` wear notifications while retaining the existing `2B03`
handling. ... I confirmed that FreeClip 2 uses the same two wear flags.
`in_ear` is true when both earbuds are worn.」解析方式沿用 PR #138。

修复方式
--------
把该型号的 `OfbHuaweiStateInEarHandler` 换成一个子类：
  * `commands` 同时包含 `2B03` 与 `2B25`（老型号行为不变）；
  * `2B25` → 解析 param 1/2，两只都在耳才算佩戴，并额外记录左右耳标志；
  * `on_init` 不再写死 false，而是写 `unknown` → UI 显示「佩戴状态未知」，
    在收到首个通知前不谎报。
`handler_id` 保持 `tws_in_ear`，所以 `huawei_features` 的能力矩阵不受影响
（佩戴能力仍然存在，控件照常显示）。

作用域：仅 `PATCHED_MODELS` 里的型号；其余型号完全走上游逻辑。
"""

from __future__ import annotations

import asyncio
import json

from ._vendor import ensure_vendor_on_path

ensure_vendor_on_path()

from openfreebuds.driver.huawei.handler.state_in_ear import (  # noqa: E402
    OfbHuaweiStateInEarHandler,
)
from openfreebuds.driver.huawei.package import HuaweiSppPackage  # noqa: E402

#: FreeClip 2 的佩戴通知命令（0x2B25）
WEAR_CMD = b"+\x25"

#: 需要打补丁的型号（值仅用于日志说明）
PATCHED_MODELS = {
    "HUAWEI FreeClip 2": "0x2B25 左右耳佩戴通知（PR #144）",
}


class PerEarWearHandler(OfbHuaweiStateInEarHandler):
    """在旧 2B03 语义之上，增加 FreeClip 2 的 2B25 左右耳佩戴解析。

    v3.26：连接后**主动查询一次**佩戴状态。原因：2B25 是通知包，只在状态
    变化时上报；如果耳机连上时就已经戴着，一条通知都不会来，界面会一直停在
    「未知 / 未佩戴」，直到用户摘下一次再戴上。上游的 2B03 handler 也只在
    `on_init` 里写死 false、不发查询，所以这里补一次 read 请求。
    """

    handler_id = "tws_in_ear"
    commands = [b"+\x03", WEAR_CMD]

    #: 查询超时：两个候选命令各试一次，总耗时控制在 handler 的 3s init 超时内
    query_timeout = 0.9

    async def on_init(self):
        # 不再写死 false：未知就是未知，UI 会显示「佩戴状态未知」
        await self.driver.put_property("state", "in_ear", "unknown")
        for cmd, params in ((WEAR_CMD, [1, 2]), (b"+\x03", [8, 9])):
            try:
                rq = HuaweiSppPackage.read_rq(cmd, params)
                resp = await asyncio.wait_for(
                    self.driver.send_package(rq), timeout=self.query_timeout)
            except Exception:
                continue
            if resp is None:
                continue
            try:
                await self.on_package(resp)
            except Exception:
                continue
            # 拿到明确状态就不再试下一个命令
            try:
                raw = await self.driver.get_property("state", "in_ear")
            except Exception:
                raw = None
            if str(raw).strip().strip('"').lower() not in ("", "unknown", "none"):
                break

    async def on_package(self, package):
        if package.command_id == WEAR_CMD:
            # 参数布局在不同固件上不完全一致：PR #144 记录的是 1/2，
            # 上游 2B03 用的是 8/9，这里两种都认。
            first = package.find_param(1)
            second = package.find_param(2)
            if not (len(first) == len(second) == 1):
                first = package.find_param(8)
                second = package.find_param(9)
            if len(first) == len(second) == 1:
                left = first[0] == 1
                right = second[0] == 1
                await self.driver.put_property(
                    "state", "in_ear", json.dumps(left and right))
                await self.driver.put_property(
                    "state", "in_ear_left", json.dumps(left))
                await self.driver.put_property(
                    "state", "in_ear_right", json.dumps(right))
            return
        # 2B03：老型号语义，交给上游实现
        await super().on_package(package)


def patch_driver(driver, model: str) -> bool:
    """把驱动里的佩戴 handler 换成 PerEarWearHandler。

    必须在 `driver.start()` **之前**调用：handler 的 commands → handler
    映射是在 `_start_all_handlers()` 里建立的。
    返回是否真的替换过（便于写日志）。
    """
    if model not in PATCHED_MODELS:
        return False
    handlers = getattr(driver, "handlers", None)
    if not handlers:
        return False
    for idx, handler in enumerate(list(handlers)):
        if isinstance(handler, OfbHuaweiStateInEarHandler) and \
                not isinstance(handler, PerEarWearHandler):
            handlers[idx] = PerEarWearHandler()
            return True
    return False


def patched_models() -> list:
    return sorted(PATCHED_MODELS)
