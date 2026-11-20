"""重新生成 wushuang-xiaoai-deploy.zip（NAS 部署源码包）。

只打包部署必需文件：wushuang 包（含模板/静态/vendor）、Docker 构建文件、
上游拉取脚本、文档与测试。reference/ 上游源码不进包（NAS 上由
scripts/fetch-upstream.sh 拉取，重建时 ncm-api 走缓存）。

用法：python scripts/build_deploy_zip.py [输出路径]
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "wushuang-xiaoai-deploy.zip"

TOP_FILES = [
    "pyproject.toml", "Dockerfile", "compose.yaml", ".env.example",
    "README.md", "LICENSE", "NOTICE", ".gitignore",
    "docker/ncm-api/Dockerfile",
    "docker/kugou-api/Dockerfile",
    "docker/qq-api/Dockerfile",
    "docker/qishui-api/Dockerfile",
    "scripts/fetch-upstream.ps1", "scripts/fetch-upstream.sh", "scripts/smoke.py",
    "docs/architecture.md", "docs/deployment-zh.md",
]


def collect() -> list[Path]:
    files: list[Path] = []
    for rel in TOP_FILES:
        p = ROOT / rel
        if p.is_file():
            files.append(p)
    for p in sorted((ROOT / "wushuang").rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        if p.suffix in (".py", ".html", ".css", ".js", ".md"):
            files.append(p)
    for p in sorted((ROOT / "tests").glob("test_*.py")):
        files.append(p)
    return files


def main() -> None:
    files = collect()
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, p.relative_to(ROOT).as_posix())
    print(f"WROTE {OUT} ({len(files)} files, {OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
