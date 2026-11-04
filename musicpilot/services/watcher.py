"""自动刮削：轮询源目录，新文件稳定后就自动排一个刮削任务。

用轮询而不是 inotify：容器里的源目录通常是 NFS / SMB / FUSE 挂载，
文件系统事件在这些场景下并不可靠，而一次目录遍历对个人音乐库来说足够便宜。

判定「新文件」的依据是（路径, 大小, mtime）。记下之后就不再重复处理，
即使文件因为移动 / 硬链接而消失了也一样——它已经被刮削过。
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from musicpilot.adapters.scanner import scan_files
from musicpilot.config import RuntimeSettings
from musicpilot.domain.models import JobKind
from musicpilot.services.jobs import JobConflict, JobManager
from musicpilot.services.scraper import JobRequest
from musicpilot.services.settings_service import SettingsError, SettingsService

logger = logging.getLogger("musicpilot.watcher")

# 每次最多排这么多文件，避免一次性把上千个旧文件全塞进一个任务
MAX_BATCH = 500
# 已经处理过的文件指纹上限，防止无限增长
_MAX_SEEN = 200_000


class AutoScraper:
    """Background loop; one instance per process, owned by the lifespan."""

    def __init__(self, jobs: JobManager, settings: SettingsService) -> None:
        self.jobs = jobs
        self.settings = settings
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._seen: dict[str, tuple[int, float]] = {}
        self._signature: tuple[str, ...] | None = None
        self._last_sync = 0.0

        self.running = False
        self.enabled = False
        self.scanning = False
        self.last_scan: float | None = None
        self.last_job_id: str | None = None
        self.last_error = ""
        self.detected = 0

    # ---- lifecycle ------------------------------------------------------
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="musicpilot-watcher")

    async def shutdown(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        self.running = False

    def kick(self) -> None:
        """Ask the loop to look at the directory right away."""
        self._wake.set()

    def applied(self, runtime: RuntimeSettings) -> None:
        """Reflect a settings change immediately instead of on the next poll."""
        self.enabled = runtime.watch_enabled
        self._last_sync = time.time()
        self.kick()

    # ---- introspection --------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": self.running,
            "scanning": self.scanning,
            "last_scan": self.last_scan,
            "last_job_id": self.last_job_id,
            "last_job_label": self.jobs.label(self.last_job_id) if self.last_job_id else "",
            "last_error": self.last_error,
            "detected": self.detected,
            "tracked": len(self._seen),
        }

    def mark_processed(self, paths: list[str]) -> None:
        """Treat these files as done so the watcher never picks them up again."""
        for value in paths:
            self._remember(value)

    def reset(self) -> None:
        """Forget everything seen so the next scan treats the library as new."""
        self._seen.clear()
        self._signature = None
        self.detected = 0
        self.kick()

    # ---- loop -----------------------------------------------------------
    async def _loop(self) -> None:
        self.running = True
        while True:
            try:
                self._sync_settings()
                if self.enabled and self._settle_ready():
                    await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the loop must survive anything
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("自动刮削循环出错")
            await self._sleep(self._interval())

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=max(1.0, seconds))
        except TimeoutError:
            pass
        finally:
            self._wake.clear()

    def _interval(self) -> float:
        try:
            return self.settings.get().watch_interval
        except SettingsError:
            return 30.0

    def _settle_ready(self) -> bool:
        """Wait ``watch_settle`` seconds after a start/toggle switch before scanning."""
        now = time.time()
        if self._last_sync == 0.0:
            self._last_sync = now
            return False
        try:
            return now - self._last_sync >= self.settings.get().watch_settle
        except SettingsError:
            return False

    def _sync_settings(self) -> None:
        runtime = self.settings.get()
        signature = (str(runtime.source_root), str(runtime.library_root))
        if self._signature != signature:
            if self._signature is not None:
                self._seen.clear()
            self._signature = signature
            self._last_sync = time.time()
        self.enabled = runtime.watch_enabled

    async def _tick(self) -> None:
        if self.jobs.is_busy() or self.scanning:
            return
        self.scanning = True
        try:
            runtime = self.settings.get()
            root = runtime.source_root
            if not root.is_dir():
                self.last_error = f"源目录不存在：{root}"
                return
            found, _warnings = await asyncio.to_thread(
                scan_files,
                root,
                extensions=self.jobs.scraper.base.extensions,
                exclude_dirs=self.jobs.scraper.base.excluded_directories,
                max_files=self.jobs.scraper.base.max_scan_files,
            )
            fresh: list[Path] = []
            for audio in found:
                fingerprint = self._fingerprint(str(audio))
                if fingerprint is None:
                    continue
                if self._seen.get(str(audio)) == fingerprint:
                    continue
                if self._unstable(audio):
                    continue
                fresh.append(audio)
            self.last_scan = time.time()
            self.last_error = ""
            if not fresh:
                return
            batch = sorted(fresh)[:MAX_BATCH]
            job = self.jobs.start(
                JobRequest(kind=JobKind.AUTO, paths=[str(path) for path in batch]),
                title=batch[0].name if len(batch) == 1 else "",
            )
            self.mark_processed([str(path) for path in batch])
            self.last_job_id = job.id
            self.detected += len(batch)
            logger.info("自动刮削：已排入 %s 个新文件，任务 %s", len(batch), job.id)
        except JobConflict:
            return  # 手动任务在跑，下一轮再看
        except SettingsError as exc:
            self.last_error = str(exc)
        finally:
            self.scanning = False

    @staticmethod
    def _fingerprint(path: str) -> tuple[int, float] | None:
        try:
            stat = Path(path).stat()
        except OSError:
            return None
        return stat.st_size, round(stat.st_mtime, 3)

    def _unstable(self, path: Path) -> bool:
        """A file still being written/downloaded gets another chance next round."""
        try:
            stat = path.stat()
        except OSError:
            return True
        settle = self.settings.get().watch_settle
        return settle > 0 and time.time() - stat.st_mtime < settle

    def _remember(self, path: str) -> None:
        self._seen[path] = self._fingerprint(path) or (0, 0.0)
        if len(self._seen) > _MAX_SEEN:
            for key in list(self._seen)[: _MAX_SEEN // 10]:
                self._seen.pop(key, None)
