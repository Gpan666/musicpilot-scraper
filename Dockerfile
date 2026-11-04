FROM python:3.12-slim

# 构建时可换用更快的 PyPI 镜像：docker build --build-arg PIP_INDEX_URL=...
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MP_DATA_DIR=/data \
    MP_SOURCE_DIR=/media/downloads \
    MP_LIBRARY_DIR=/media/music \
    MP_STATIC_DIR=/app/web

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY musicpilot ./musicpilot
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" . \
    && mkdir -p /data /media/downloads /media/music
COPY web ./web

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/api/health', timeout=5).read()" || exit 1
CMD ["uvicorn", "musicpilot.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
