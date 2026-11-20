# 无双小爱同学 主服务镜像
FROM python:3.12-slim

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
