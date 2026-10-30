"""
应用元信息（版本 / 作者）。

集中放在一处：浮窗的「关于」页、构建脚本、README 都引用这里，
避免版本号散落在多个文件里对不上。
"""

from __future__ import annotations

APP_NAME = "VivoPods Controller"
#: 对外展示的软件版本（关于页第一行：软件版本 V11.45.80）
APP_VERSION = "11.45.80"
#: 关于页第二行
APP_AUTHOR = "芝士火腿肠拌饭"


def version_text() -> str:
    return f"软件版本 V{APP_VERSION}"


def author_text() -> str:
    return f"本软件作者：{APP_AUTHOR}"
