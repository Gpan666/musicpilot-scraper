"""Application assembly: dependency wiring, router registration, static hosting.

The whole HTTP layer is ~10 small modules; there is no 12k-line ``create_app``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from musicpilot import __version__
from musicpilot.adapters.metadata.service import MetadataService
from musicpilot.adapters.store import Store
from musicpilot.api import routes_files, routes_jobs, routes_meta, routes_settings
from musicpilot.api.auth import bootstrap_admin, require_session
from musicpilot.api.auth import router as auth_router
from musicpilot.api.context import AppContext, resolve_secret
from musicpilot.config import load_settings
from musicpilot.services.jobs import JobManager
from musicpilot.services.scraper import ScraperService
from musicpilot.services.settings_service import SettingsService

logger = logging.getLogger("musicpilot")


def create_app() -> FastAPI:
    base = load_settings()
    logging.basicConfig(level=getattr(logging, base.log_level.upper(), logging.INFO))

    store = Store(base.database_path)
    store.initialize()
    bootstrap_admin(store, base)
    secret = resolve_secret(base)
    metadata = MetadataService(base, store)
    scraper = ScraperService(base, store, metadata)
    settings_service = SettingsService(base, store)
    jobs = JobManager(store, scraper, settings_service)
    context = AppContext(
        base=base,
        store=store,
        settings=settings_service,
        metadata=metadata,
        scraper=scraper,
        jobs=jobs,
        secret=secret,
        auth_enabled=True,
        started_at=time.time(),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await metadata.startup()
        logger.info("MusicPilot Scraper %s ready", __version__)
        try:
            yield
        finally:
            await jobs.shutdown()
            await metadata.shutdown()

    app = FastAPI(
        title=base.app_name,
        version=__version__,
        description="只做音乐刮削与整理的自托管工具（支持复制/移动/硬链接/软链接）",
        lifespan=lifespan,
    )
    app.state.context = context

    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "服务器内部错误，请查看日志", "error": type(exc).__name__},
        )

    # Public endpoints: health probe (Docker) and authentication.
    app.include_router(routes_meta.public_router)
    app.include_router(auth_router)

    # Everything below requires a session when authentication is enabled.
    protected = APIRouter(prefix="/api", dependencies=[Depends(require_session)])
    protected.include_router(routes_meta.router)
    protected.include_router(routes_settings.router)
    protected.include_router(routes_files.router)
    protected.include_router(routes_jobs.router)
    app.include_router(protected)

    static_dir = base.static_dir
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    else:

        @app.get("/", response_class=HTMLResponse)
        def missing_frontend() -> str:
            return (
                "<h1>MusicPilot Scraper</h1>"
                f"<p>静态前端目录不存在：{static_dir}</p>"
                "<p>API 文档请访问 <a href='/docs'>/docs</a></p>"
            )

    return app
