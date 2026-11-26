"""构建桌面版便携程序（Windows / macOS / Linux 通用，onedir 目录版）。

    python scripts/build_desktop.py            # 当前平台
    python scripts/build_desktop.py --skip-deps

产物在 dist/desktop/（每个平台一个便携 zip，解压到任意可写文件夹双击即用）：
- Windows  无双小爱同学-桌面版-vX.Y.Z-Windows.zip
- macOS    无双小爱同学-桌面版-vX.Y.Z-macOS-<arch>.app.zip
- Linux    无双小爱同学-桌面版-vX.Y.Z-Linux-<arch>.zip

为什么用 onedir 而非单文件 onefile：onefile 每次启动都要向系统 TEMP 解压，
在部分安全软件/受限环境下会报 "Could not create temporary directory!"。
onedir 不解压、数据存程序目录内，彻底规避该问题，启动也更快。

跨平台说明：PyInstaller 不支持交叉编译，各平台产物需在对应系统
（或 CI，见 .github/workflows/desktop.yml）上执行本脚本。
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows 控制台可能是 GBK：避免 emoji/中文输出崩溃
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from wushuang import __version__  # noqa: E402


def ensure_deps() -> None:
    need = []
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        need.append("pyinstaller>=6.6")
    try:
        import webview  # noqa: F401
    except ImportError:
        need.append("pywebview>=5.1")
    try:
        import pystray  # noqa: F401
    except ImportError:
        need.append("pystray>=0.19")
    try:
        import PIL  # noqa: F401
    except ImportError:
        need.append("Pillow>=10")
    if sys.platform == "win32":
        # pythonnet 3.0.x 与 pywebview 5.x 的 WebView2 桥接配合最稳
        need.append("pythonnet==3.0.3")
    if need:
        print("安装构建依赖:", ", ".join(need))
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "--no-cache-dir",
            "--prefer-binary", *need,
        ])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-deps", action="store_true")
    args = ap.parse_args()

    if not args.skip_deps:
        ensure_deps()

    # 图标（幂等）
    subprocess.check_call([sys.executable, str(ROOT / "scripts" / "make_icon.py")])

    dist = ROOT / "dist" / "desktop"
    work = ROOT / "build" / "desktop"
    subprocess.check_call([
        sys.executable, "-m", "PyInstaller",
        str(ROOT / "desktop.spec"),
        "--noconfirm", "--clean",
        "--distpath", str(dist),
        "--workpath", str(work),
    ], cwd=str(ROOT))

    system = platform.system()
    arch = platform.machine().lower()

    def portable(src_name: str, label: str, exe_rel: str | None = None) -> None:
        """onedir 文件夹 → 重命名 → 打便携 zip。"""
        src = dist / src_name
        if not src.exists():
            raise SystemExit(f"未找到构建产物: {src}")
        target = dist / f"无双小爱同学-桌面版-v{__version__}-{label}"
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(src), str(target))
        if exe_rel:
            exe = target / exe_rel
            if exe.exists():
                exe.chmod(0o755)
        if system == "Windows":
            # .NET Framework 可能把非系统盘/下载解压文件判为"网络位置"而拒绝
            # 加载 Python.Runtime.dll（0x80131515），内置 WebView2 窗口随之失效。
            # 随包附带 exe.config 打开 loadFromRemoteSources 开关（运行时也会兜底补写）。
            from wushuang.desktop import _NETFX_CONFIG
            (target / "无双小爱同学.exe.config").write_text(
                _NETFX_CONFIG, encoding="utf-8")
        zip_path = shutil.make_archive(str(target), "zip",
                                       root_dir=str(dist), base_dir=target.name)
        print("\n[OK] 便携目录:", target)
        print("[OK] 便携压缩包:", zip_path)
        print("     解压到任意可写文件夹，双击其中的 exe 即可运行（无需安装）。")

    if system == "Windows":
        portable("无双小爱同学", "Windows", "无双小爱同学.exe")
    elif system == "Darwin":
        portable("无双小爱同学.app", f"macOS-{arch}.app")
    else:
        portable("无双小爱同学", f"Linux-{arch}", "无双小爱同学")
        print("Linux 运行需系统 WebKitGTK：sudo apt install gir1.2-webkit2-4.1 gir1.2-gtk-3.0")
        print("（无图形环境可加 --browser 参数，用系统浏览器打开后台）")


if __name__ == "__main__":
    main()