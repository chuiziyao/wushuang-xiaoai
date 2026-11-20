#!/usr/bin/env bash
# 拉取部署所需的上游组件源码到 reference/（docker compose 构建用）。
# 在 NAS SSH 或本机执行：bash scripts/fetch-upstream.sh
set -e
cd "$(dirname "$0")/.."
mkdir -p reference

fetch() {
  name="$1"; repo="$2"; branch="${3:-main}"
  if [ -d "reference/$name" ]; then
    echo "reference/$name 已存在，跳过（删除该目录可重新拉取）"
    return
  fi
  echo "==> 拉取 $repo ($branch)"
  if command -v git >/dev/null 2>&1; then
    if git clone --depth 1 -b "$branch" "https://github.com/$repo.git" "reference/$name"; then
      return
    fi
    echo "git 克隆失败，改用 tarball…"
    rm -rf "reference/$name"
  fi
  curl -sSL --retry 3 --connect-timeout 20 -o "reference/$name.tar.gz" \
    "https://codeload.github.com/$repo/tar.gz/refs/heads/$branch"
  tar -xzf "reference/$name.tar.gz" -C reference
  mv "reference/$name-$branch" "reference/$name"
  rm -f "reference/$name.tar.gz"
}

fetch ncm-api-enhanced Catamint/ncm-api-enhanced
fetch doubao-free-api-1 sxkiss/doubao-free-api-1
fetch Qwen-Free-API xiaoY233/Qwen-Free-API

# ncm-api 使用本项目定制的构建文件（国内 registry 支持）
cp docker/ncm-api/Dockerfile reference/ncm-api-enhanced/Dockerfile.wushuang
# free-api 通用构建文件（修复新 node 镜像无 yarn 问题）
cp docker/free-api/Dockerfile reference/doubao-free-api-1/Dockerfile.wushuang
cp docker/free-api/Dockerfile reference/Qwen-Free-API/Dockerfile.wushuang

echo ""
echo "完成。接下来：cp .env.example .env && 编辑 .env && docker compose up -d --build"
echo "开发参考（可选）：hanxi/xiaomusic、idootop/mi-gpt 可用同样方式拉取。"
