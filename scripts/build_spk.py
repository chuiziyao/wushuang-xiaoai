"""构建「无双小爱同学」群晖 SPK 套件（在开发机运行）。

用法：python scripts/build_spk.py [--version 0.1.0-0001]
输出：dist/wushuang_xiaoai-<version>.spk

结构遵循 Synology Package Developer Guide（DSM 7.2）：
- INFO + package.tgz + scripts/ + conf/(privilege,resource) + WIZARD_UIFILES + 图标
- conf/resource 使用 Docker Project worker，由 Container Manager 原生管理
  compose 项目的构建/启停/删除
- 持久数据走 DSM FHS：var/（数据库/密钥）与 etc/（wushuang.env）
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import tarfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SPK_SRC = ROOT / "spk"
STAGE = ROOT / "build" / "spk-stage"
DIST = ROOT / "dist"

IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "node_modules", ".venv",
                                "交付总结.md")  # 排除含部署实例隐私的个人文档
ICON_SIZES = [16, 24, 32, 48, 64, 72, 256]

FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for p in FONT_CANDIDATES:
        if Path(p).is_file():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def make_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = max(2, size // 8)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius,
                        fill=(28, 36, 49, 255))
    if size >= 48:
        f1 = _font(int(size * 0.34))
        f2 = _font(int(size * 0.13))
        d.text((size / 2, size * 0.40), "无双", font=f1, fill=(255, 255, 255, 255),
               anchor="mm")
        d.text((size / 2, size * 0.72), "小爱同学", font=f2, fill=(159, 176, 200, 255),
               anchor="mm")
    else:
        f1 = _font(int(size * 0.62))
        d.text((size / 2, size / 2), "无", font=f1, fill=(255, 255, 255, 255),
               anchor="mm")
    return img


def _copytree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, ignore=IGNORE, dirs_exist_ok=True)


def build(version: str) -> Path:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    target = STAGE / "target"
    proj = target / "project"
    proj.mkdir(parents=True)

    # ---- project/（compose 项目根）----
    shutil.copy2(SPK_SRC / "compose.yaml", proj / "compose.yaml")
    for name in ("Dockerfile", "pyproject.toml", "README.md", "LICENSE", "NOTICE"):
        shutil.copy2(ROOT / name, proj / name)
    _copytree(ROOT / "wushuang", proj / "wushuang")
    _copytree(ROOT / "docker", proj / "docker")
    _copytree(ROOT / "docs", proj / "docs")
    for ref in ("ncm-api-enhanced", "KuGouMusicApi", "QQMusicApi", "qishui-api"):
        src = ROOT / "reference" / ref
        if not src.is_dir():
            raise SystemExit(f"缺少上游源码 reference/{ref}，先运行 scripts/fetch-upstream")
        _copytree(src, proj / "reference" / ref)
    # QQMusicApi Web 服务的运行时数据（设备指纹/凭据状态）绝不打入套件
    qq_data = proj / "reference" / "QQMusicApi" / "web" / "data"
    if qq_data.exists():
        shutil.rmtree(qq_data)
    # 定制构建文件随包分发（豆包/元宝已内置，无需中转容器源码）
    for ref, docker_dir in (("ncm-api-enhanced", "ncm-api"),
                            ("KuGouMusicApi", "kugou-api"),
                            ("QQMusicApi", "qq-api"),
                            ("qishui-api", "qishui-api")):
        shutil.copy2(ROOT / "docker" / docker_dir / "Dockerfile",
                     proj / "reference" / ref / "Dockerfile.wushuang")

    # ---- ui/（DSM 桌面集成）----
    ui = target / "ui"
    _copytree(SPK_SRC / "ui", ui)
    (ui / "images").mkdir(exist_ok=True)
    for s in ICON_SIZES:
        make_icon(s).save(ui / "images" / f"icon_{s}.png")

    # ---- package.tgz ----
    pkg_tgz = STAGE / "package.tgz"
    with tarfile.open(pkg_tgz, "w:gz", format=tarfile.GNU_FORMAT) as tar:
        for child in sorted(target.iterdir()):
            tar.add(child, arcname=child.name)
    checksum = hashlib.md5(pkg_tgz.read_bytes()).hexdigest()
    extract_kb = sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) // 1024

    # ---- INFO ----
    info = f'''package="wushuang_xiaoai"
version="{version}"
arch="noarch"
description="XiaoAI speaker enhancement: LLM chat (Doubao built-in) + free music with NetEase/Kugou/QQ/Soda playlist sync. Unified web admin on port 8080."
description_chs="小爱音箱增强：大模型对话（豆包内置）+ 自由听歌（网易云/酷狗/QQ/汽水歌单定时同步），统一网页后台，端口 8080。"
displayname="Wushuang XiaoAI"
displayname_chs="无双小爱同学"
maintainer="wushuang-xiaoai contributors"
os_min_ver="7.2-69057"
install_dep_packages="ContainerManager"
adminport="8080"
adminprotocol="http"
dsmuidir="ui"
dsmappname="com.wushuang.xiaoai"
checkport="no"
ctl_stop="yes"
silent_install="no"
silent_upgrade="yes"
checksum="{checksum}"
extractsize="{extract_kb}"
'''
    (STAGE / "INFO").write_text(info, encoding="utf-8")

    # ---- scripts / conf / wizard / LICENSE / 图标 ----
    _copytree(SPK_SRC / "scripts", STAGE / "scripts")
    _copytree(SPK_SRC / "conf", STAGE / "conf")
    _copytree(SPK_SRC / "WIZARD_UIFILES", STAGE / "WIZARD_UIFILES")
    shutil.copy2(ROOT / "LICENSE", STAGE / "LICENSE")
    make_icon(64).save(STAGE / "PACKAGE_ICON.PNG")
    make_icon(256).save(STAGE / "PACKAGE_ICON_256.PNG")

    # ---- SPK（外层 tar，INFO 优先）----
    DIST.mkdir(exist_ok=True)
    spk_path = DIST / f"wushuang_xiaoai-{version}.spk"
    members = ["INFO", "package.tgz", "scripts", "conf", "WIZARD_UIFILES",
               "PACKAGE_ICON.PNG", "PACKAGE_ICON_256.PNG", "LICENSE"]
    with tarfile.open(spk_path, "w", format=tarfile.GNU_FORMAT) as tar:
        for name in members:
            tar.add(STAGE / name, arcname=name)
    print(f"SPK 构建完成: {spk_path} ({spk_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return spk_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="0.2.0-0001")
    args = ap.parse_args()
    build(args.version)
