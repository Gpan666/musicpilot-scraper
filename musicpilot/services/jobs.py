"""Single-at-a-time background job queue with cancellation and persisted progress."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from musicpilot.adapters.store import Store
from musicpilot.domain.models import JobKind, JobSnapshot, JobStatus
from musicpilot.services.scraper import JobRequest, ScraperService
from musicpilot.services.settings_service import SettingsService


class JobConflict(RuntimeError):
    pass


class JobManager:
    def __init__(
        self,
        store: Store,
        scraper: ScraperService,
        settings: SettingsService,
    ) -> None:
        self.store = store
        self.scraper = scraper
        self.settings = settings
        self._jobs: dict[str, JobSnapshot] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._events: dict[str, asyncio.Event] = {}
        self._active_id: str | None = None
        self._watcher: Any | None = None

    def attach_watcher(self, watcher: Any) -> None:
        """Wire the directory watcher in once, after both objects exist."""
        self._watcher = watcher

    def _requires_idle(self, request: JobRequest) -> bool:
        """Previews and manual scrapes must not race; the watcher just waits its turn."""
        return request.kind is not JobKind.AUTO

    def is_busy(self) -> bool:
        if self._active_id is None:
            return False
        active = self._jobs.get(self._active_id)
        return bool(
            active and active.status in {JobStatus.PENDING.value, JobStatus.RUNNING.value}
        )

    def start(
        self,
        request: JobRequest,
        choices: dict[str, dict[str, Any]] | None = None,
        *,
        title: str = "",
    ) -> JobSnapshot:
        if self._requires_idle(request) and self.is_busy():
            active = self._jobs[self._active_id]  # type: ignore[index]
            raise JobConflict(f"已有任务 {active.id} 正在运行，请等待完成或先取消")
        runtime = self.settings.get()
        job = JobSnapshot(
            id=uuid.uuid4().hex[:12],
            kind=request.kind.value,
            status=JobStatus.PENDING.value,
            total=0,
            done=0,
            failed=0,
            skipped=0,
            created_at=time.time(),
            dry_run=request.dry_run,
            message="任务已排队",
            title=title,
            auto=request.kind is JobKind.AUTO,
        )
        self._jobs[job.id] = job
        self._events[job.id] = asyncio.Event()
        self.store.save_job(job, request.as_dict())
        self._active_id = job.id
        self._tasks[job.id] = asyncio.create_task(
            self._run(job.id, request, runtime, choices or {})
        )
        return job

    def _make_progress(self, job_id: str) -> Any:
        def update(processed: int, failed: int, skipped: int, current: str, message: str) -> None:
            job = self._jobs.get(job_id)
            if job is None:
                return
            done = max(0, processed - failed - skipped)
            title = job.title or self._title_for(job_id, current, processed)
            updated = replace(
                job,
                processed=processed,
                done=done,
                failed=failed,
                skipped=skipped,
                current_file=current,
                message=message,
                title=title,
            )
            self._jobs[job_id] = updated
            self.store.update_progress(
                job_id,
                processed=processed,
                done=done,
                failed=failed,
                skipped=skipped,
                current_file=current,
                message=message,
                title=title,
            )

        return update

    def _title_for(self, job_id: str, current: str, processed: int = 0) -> str:
        """A human-readable label: the single file name, or a plain count.

        一次处理多个文件时只写「几个文件」，不写某个文件名——标题写死成第一个
        文件名，任务记录里就会冒出一堆「同一个文件名」的条目，和小标题重复又误导。
        """
        raw = (self.store.get_job(job_id) or {}).get("options") or "{}"
        try:
            requested = [value for value in (json.loads(raw).get("paths") or []) if value]
        except (TypeError, ValueError, AttributeError):
            requested = []
        if len(requested) == 1:
            return Path(requested[0]).name
        if len(requested) > 1 or (not current and processed > 1):
            return ""
        return Path(current).name if current else ""

    async def _run(
        self,
        job_id: str,
        request: JobRequest,
        runtime: Any,
        choices: dict[str, dict[str, Any]],
    ) -> None:
        event = self._events[job_id]
        self._jobs[job_id] = replace(
            self._jobs[job_id], status=JobStatus.RUNNING.value, message="正在扫描与刮削"
        )
        self.store.update_progress(job_id, status=JobStatus.RUNNING.value, message="正在扫描与刮削")
        status = JobStatus.COMPLETED.value
        message = "任务完成"
        try:
            warnings = await self.scraper.execute(
                job_id,
                request,
                runtime,
                progress=self._make_progress(job_id),
                cancel_event=event,
                choices=choices,
            )
            if event.is_set():
                status = JobStatus.CANCELLED.value
                message = "任务已取消"
            elif warnings:
                message = "；".join(warnings[:5])
        except asyncio.CancelledError:
            status = JobStatus.CANCELLED.value
            message = "任务已取消"
        except Exception as exc:  # noqa: BLE001 - surface the failure in the job record
            status = JobStatus.FAILED.value
            message = f"任务失败：{type(exc).__name__}: {exc}"
        finally:
            previous = self._jobs.get(job_id)
            if previous is not None:
                final = replace(
                    previous,
                    status=status,
                    finished_at=time.time(),
                    message=message,
                )
                self._jobs[job_id] = final
                self.store.save_job(final)
            self._tasks.pop(job_id, None)
            self._events.pop(job_id, None)
            if self._active_id == job_id:
                self._active_id = None
            if self._watcher is not None:
                # 任务收尾立刻把指纹写进库，再唤醒监控：中途重启也不会重刮一遍
                self._watcher.flush()
                self._watcher.kick()

    def get(self, job_id: str) -> dict[str, Any] | None:
        job = self._jobs.get(job_id)
        if job is not None:
            return job.as_dict()
        return self.store.get_job(job_id)

    def list(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.store.list_jobs(limit)
        for index, row in enumerate(rows):
            live = self._jobs.get(str(row["id"]))
            if live is not None:
                rows[index] = {**row, **live.as_dict()}
        return rows

    def items(self, job_id: str, *, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        return self.store.list_items(job_id, limit=limit, offset=offset)

    def label(self, job_id: str) -> str:
        """A display name for a job: the single file name, else "几个文件"."""
        job = self.get(job_id) or {}
        title = str(job.get("title") or "").strip()
        if title:
            return Path(title).name
        processed = int(job.get("processed") or 0)
        current = str(job.get("current_file") or "").strip()
        if processed <= 1 and current:
            return Path(current).name
        kind = str(job.get("kind") or "")
        prefix = "自动刮削" if kind == JobKind.AUTO.value else "刮削"
        return f"{prefix} · {processed} 个文件" if processed else prefix

    def delete(self, job_id: str) -> bool:
        """删除一条已结束的任务记录；正在跑的任务要先取消。"""
        if job_id in self._tasks or job_id == self._active_id:
            raise JobConflict("任务正在运行，请先取消再删除")
        removed = self.store.delete_job(job_id)
        self._jobs.pop(job_id, None)
        return removed

    def clear(self) -> int:
        """清空任务记录，正在跑的那条保留。"""
        removed = self.store.delete_finished_jobs(keep_active=True)
        for job_id in list(self._jobs):
            if job_id not in self._tasks and job_id != self._active_id:
                self._jobs.pop(job_id, None)
        return removed

    def cancel(self, job_id: str) -> bool:
        event = self._events.get(job_id)
        if event is None:
            return False
        event.set()
        return True

    async def shutdown(self) -> None:
        for event in self._events.values():
            event.set()
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._events.clear()
        self._active_id = None
