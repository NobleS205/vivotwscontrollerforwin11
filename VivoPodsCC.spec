# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：把 VivoPodsCC 打成单文件 EXE。

用法（项目根目录）：
    pyinstaller --noconfirm --clean VivoPodsCC.spec
产物：dist\\VivoPodsCC.exe（64 位、单文件、无控制台窗口）

说明：
- ws2_32.dll / bthprops.cpl 是 Windows 系统组件，无需打包；
- 这里不用 --collect-all PySide6（会把整个 Qt 塞进去、体积巨大），
  而是用 PyInstaller 自带的 PySide6 hook 按实际 import 收集所需模块；
- 需要 PyInstaller >= 6.0（由 build.bat 自动安装）。

v3.17（并入华为）关键点：
- `huawei/vendor/` 下的 OpenFreebuds 是**运行时才加入 sys.path** 的
  （见 huawei/_vendor.py），PyInstaller 的静态分析看不到它。因此必须把
  `huawei/vendor` 加进 pathex 并显式 collect_submodules("openfreebuds")，
  否则 EXE 启动后一选华为耳机就 ImportError。
- `pods/` 是新增的多品牌统一控制层，同样整包收集。
"""

import os
import sys

from PyInstaller.utils.hooks import collect_submodules

# ---- 让静态分析阶段也能 import 到 vendored 的 OpenFreebuds ------------- #
_VENDOR_DIR = os.path.join(SPECPATH, "huawei", "vendor")
if _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

hiddenimports = (
    # 项目自身的包（pods 为 v3.17 新增）
    collect_submodules("vivo")
    + collect_submodules("huawei")
    + collect_submodules("pods")
    + collect_submodules("ui")
    # vendored 上游驱动：只在运行时经 sys.path 注入，静态分析看不见
    + collect_submodules("openfreebuds")
    + collect_submodules("openfreebuds_backend")
    # 仅在运行时被 Qt 内部引用到的 Qt 模块
    + collect_submodules("PySide6.QtSvg")
    # v3.28 单实例（QLocalServer / QLocalSocket）在 ui/keepalive.py 里
    # 是函数内局部 import，显式收进来更稳
    + ["PySide6.QtNetwork"]
)

#: 上游 OpenFreebuds 的 HTTP-RPC / 桌面外壳（本程序不使用）；
#: 同时防止打包机装了 aiohttp 就顺带塞进 exe
_VENDOR_OPTIONAL_EXCLUDES = [
    "aiohttp", "multidict", "yarl", "frozenlist", "aiosignal",
    "openfreebuds.main", "openfreebuds.manager", "openfreebuds.ui",
    "openfreebuds_backend.windows", "openfreebuds_backend.darwin",
]

EXCLUDES = _VENDOR_OPTIONAL_EXCLUDES + [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtWebSockets", "PySide6.QtWebChannel",
    "tkinter", "pydoc_data",
]

a = Analysis(
    ["app.py"],
    pathex=[_VENDOR_DIR],
    binaries=[],
    datas=[("assets/pods.png", "assets"),
           ("assets/freeclip2.png", "assets")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VivoPodsCC",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon=None,
)
