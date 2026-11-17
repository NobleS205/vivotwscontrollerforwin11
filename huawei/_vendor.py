"""
定位 vendored 的 OpenFreebuds 源码树并使其可导入。

vendor 保留上游 `openfreebuds` / `openfreebuds_backend` 命名空间，因此只需把
`huawei/vendor` 放进 sys.path 即可 import，无需改动上游 import 语句。
"""

from __future__ import annotations

import os
import sys

VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")


def ensure_vendor_on_path() -> str:
    """把 vendor 目录加入 sys.path（幂等），返回该目录。

    打包成 EXE 后目录不存在：此时 OpenFreebuds 已由 PyInstaller 以隐藏导入的
    形式打进 PYZ（见 `VivoPodsCC.spec` 的 collect_submodules），直接 import 即可，
    这里静默跳过，不抛错。
    """
    if os.path.isdir(VENDOR_DIR) and VENDOR_DIR not in sys.path:
        sys.path.insert(0, VENDOR_DIR)
    return VENDOR_DIR
