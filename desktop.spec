# -*- mode: python ; coding: utf-8 -*-
"""无双小爱同学 桌面版 PyInstaller spec（Windows / macOS / Linux 通用）。

用法：python scripts/build_desktop.py（自动安装依赖并调用本 spec）。
产物：dist/desktop/无双小爱同学-桌面版-vX.Y.Z-<平台>[.exe]
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

ROOT = Path(SPECPATH)

datas = [
    (str(ROOT / "wushuang" / "web" / "templates"), "wushuang/web/templates"),
    (str(ROOT / "wushuang" / "web" / "static"), "wushuang/web/static"),
    (str(ROOT / "assets" / "icon.png"), "assets"),
]
datas += collect_data_files("webview")
binaries = collect_dynamic_libs("webview")

hiddenimports = collect_submodules("wushuang") + collect_submodules("uvicorn")
hiddenimports += collect_submodules("webview") + collect_submodules("pystray")
hiddenimports += [
    "aiosqlite", "mutagen", "croniter", "tzdata",
    # uvicorn[standard] 的可选加速/协议依赖（运行时按需 import）
    "httptools", "websockets", "websockets.legacy", "websockets.legacy.server",
    "watchfiles", "dotenv", "yaml", "colorama",
]
if sys.platform == "win32":
    hiddenimports += ["webview.platforms.edgechromium", "clr_loader", "pythonnet"]
elif sys.platform == "darwin":
    hiddenimports += ["webview.platforms.cocoa"]
else:
    hiddenimports += ["webview.platforms.gtk"]

a = Analysis(
    [str(ROOT / "desktop_entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "scipy", "IPython",
              "pytest", "pip"],
    noarchive=False,
)
pyz = PYZ(a.pure)

# onedir（目录版）：启动时不再向系统 TEMP 解压，彻底规避
# "Could not create temporary directory!"（安全软件/受限环境拦截 onefile 解压）。
# 产物为一个文件夹，配合 build_desktop.py 打成便携 zip；数据存程序目录内。
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="无双小爱同学",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=sys.platform != "win32",
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "icon.ico"),
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        a.binaries,
        a.datas,
        [],
        name="无双小爱同学.app",
        icon=str(ROOT / "assets" / "icon.icns"),
        bundle_identifier="com.chuiwushuang.wushuang-xiaoai",
        info_plist={
            "CFBundleDisplayName": "无双小爱同学",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
else:
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        [],
        name="无双小爱同学",
    )