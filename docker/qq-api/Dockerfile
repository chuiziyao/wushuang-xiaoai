# qq-api sidecar 构建文件（QQMusicApi Web 服务，上游 GPLv3，独立容器隔离）
# 由 scripts/fetch-upstream.* 复制为 reference/QQMusicApi/Dockerfile.wushuang
# 不用上游 Dockerfile：其依赖 ghcr.io uv 镜像与 BuildKit 缓存挂载，NAS 环境不稳。
FROM python:3.12-slim

ARG PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple/
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} uv

ENV UV_DEFAULT_INDEX=${PIP_INDEX_URL} \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0 \
    PYTHONUNBUFFERED=1 \
    QQMUSIC_SERVER_HOST=0.0.0.0

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --group web --frozen --no-install-project
COPY qqmusic_api/ qqmusic_api/
COPY web/ web/
RUN uv sync --group web --frozen

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8080
CMD ["python", "web/run.py"]
