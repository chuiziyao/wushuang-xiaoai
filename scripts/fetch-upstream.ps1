# 拉取部署所需的上游组件源码到 reference/（Windows 开发机用）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\fetch-upstream.ps1
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
New-Item -ItemType Directory -Force -Path reference | Out-Null

function Fetch-Upstream($name, $repo, $branch = 'main') {
  if (Test-Path "reference/$name") {
    Write-Host "reference/$name 已存在，跳过"
    return
  }
  Write-Host "==> 拉取 $repo ($branch)"
  $tgz = "reference/$name.tar.gz"
  curl.exe -sSL --retry 3 --connect-timeout 20 -o $tgz `
    "https://codeload.github.com/$repo/tar.gz/refs/heads/$branch"
  if ($LASTEXITCODE -ne 0) { throw "下载失败: $repo" }
  tar -xzf $tgz -C reference
  Move-Item "reference/$name-$branch" "reference/$name"
  Remove-Item $tgz
}

Fetch-Upstream 'ncm-api-enhanced' 'Catamint/ncm-api-enhanced'
Fetch-Upstream 'doubao-free-api-1' 'sxkiss/doubao-free-api-1'
Fetch-Upstream 'Qwen-Free-API' 'xiaoY233/Qwen-Free-API'

# ncm-api 使用本项目定制的构建文件（国内 registry 支持）
Copy-Item docker/ncm-api/Dockerfile reference/ncm-api-enhanced/Dockerfile.wushuang -Force
# free-api 通用构建文件（修复新 node 镜像无 yarn 问题）
Copy-Item docker/free-api/Dockerfile reference/doubao-free-api-1/Dockerfile.wushuang -Force
Copy-Item docker/free-api/Dockerfile reference/Qwen-Free-API/Dockerfile.wushuang -Force

Write-Host "`n完成。接下来：复制 .env.example 为 .env 并修改，然后 docker compose up -d --build"
