#!/bin/sh
# 统一入口：按 PUID/PGID/UMASK 准备运行用户，再降权执行 CMD。
# 挂载目录的属主不一定是镜像里的 app 用户，所以要先按需调整归属。
set -eu

RUN_UID="${PUID:-1000}"
RUN_GID="${PGID:-1000}"
RUN_UMASK="${UMASK:-022}"

if [ "$(id -u)" = "0" ]; then
    # 让镜像内的 app 用户对齐 PUID/PGID（仅在数值不同时改动，避免每次启动都 chown 整个镜像）
    if [ "$(id -g app)" != "${RUN_GID}" ]; then
        groupmod -o -g "${RUN_GID}" app
    fi
    if [ "$(id -u app)" != "${RUN_UID}" ]; then
        usermod -o -u "${RUN_UID}" app
    fi

    # 数据目录必须可写；媒体目录尽量修正，但宿主可能是只读挂载，失败不阻断启动
    mkdir -p "${MP_DATA_DIR:-/data}"
    chown -R app:app "${MP_DATA_DIR:-/data}" 2>/dev/null || true
    for dir in "${MP_SOURCE_DIR:-/media/downloads}" "${MP_LIBRARY_DIR:-/media/music}"; do
        [ -d "$dir" ] || mkdir -p "$dir" 2>/dev/null || true
        chown app:app "$dir" 2>/dev/null || true
    done

    set -- gosu app:app "$@"
fi

umask "${RUN_UMASK}"
exec "$@"
