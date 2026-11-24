# 无双小爱同学 主服务镜像
FROM python:3.12-slim

# ffmpeg：FLAC CUE 分轨需要（WAV CUE 用字节偏移不需要）
RUN sed -i 's|deb.debian.org|mirrors.cloud.tencent.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 国内环境默认用腾讯 PyPI 镜像，可用 --build-arg PIP_INDEX_URL=https://pypi.org/simple 覆盖
ARG PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple/
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WUSHUANG_DATA_DIR=/data \
    WUSHUANG_MUSIC_DIR=/music

WORKDIR /app
COPY pyproject.toml README.md ./
COPY wushuang ./wushuang
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} .

EXPOSE 8080
VOLUME ["/data", "/music"]
CMD ["python", "-m", "wushuang"]
