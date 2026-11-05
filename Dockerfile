# syntax=docker/dockerfile:1

# 多阶段构建：依赖与前端先在构建阶段装好，运行镜像只带 venv + 应用代码。
ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS builder

# 构建时可换用更快的 PyPI 镜像：docker build --build-arg PIP_INDEX_URL=...
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
# 只装运行时依赖（不含 dev 额外依赖）
COPY pyproject.toml README.md LICENSE ./
COPY musicpilot ./musicpilot
COPY web ./web
COPY scripts ./scripts
# 依赖在 amd64 / arm64 上都有官方预编译 wheel（含 uvicorn[standard] 的 httptools/uvloop），
# 镜像内无需编译 C 扩展，builder 阶段不必装 build-essential 之类的编译工具链。
# 前端随 wheel 安装到 venv 内（musicpilot/web），镜像里不再单独拷 web/
RUN python -m venv /opt/venv \
    && python scripts/sync_web.py \
    && /opt/venv/bin/pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" .

FROM python:${PYTHON_VERSION}-slim AS runtime

# 重新声明（构建阶段同名 ARG 不会自动继承），也方便 buildx 用 --build-arg 覆盖
ARG PYTHON_VERSION

# 通用运行参数：PUID/PGID 决定容器内进程的用户，宿主挂载目录写不进去时改这两个数与宿主对齐；
# UMASK 控制新建文件权限（NAS 上多人共享时给 000 或 002）。
ARG PUID=1000
ARG PGID=1000
ARG UMASK=022
ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PUID="${PUID}" \
    PGID="${PGID}" \
    UMASK="${UMASK}" \
    MP_HOST=0.0.0.0 \
    MP_PORT=8000 \
    MP_DATA_DIR=/data \
    MP_SOURCE_DIR=/media/downloads \
    MP_LIBRARY_DIR=/media/music \
    MP_BROWSABLE_ROOTS=/media

# gosu 用于从 root 降权到 PUID:PGID 后再启动进程；tzdata 让容器内 TZ 时区生效
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /usr/sbin/nologin app

COPY --from=builder --chown=app:app /opt/venv /opt/venv

WORKDIR /app
RUN mkdir -p /data /media/downloads /media/music \
    && chown -R app:app /app /data /media

# 自检：前端必须随 venv 一起进来，缺了说明打包链路断了
RUN test -f /opt/venv/lib/python"${PYTHON_VERSION}"/site-packages/musicpilot/web/index.html

# 入口脚本只用 sh + gosu，不引入 s6/supervisord 之类的进程管理器
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod 0755 /entrypoint.sh

EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/api/health', timeout=5).read()" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "musicpilot"]
