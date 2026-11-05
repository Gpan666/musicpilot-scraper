"""自动刮削：轮询源目录，出现**新增**文件且音乐库里还没有，才自动排一个刮削任务。

用轮询而不是 inotify：容器里的源目录通常是 NFS / SMB / FUSE 挂载，
文件系统事件在这些场景下并不可靠，而一次目录遍历对个人音乐库来说足够便宜。

判定只看两件事，顺序固定：

1. **源目录里是不是新增文件**（``watch_seen`` 表）。开启监控那一刻已经在的文件
   算存量，只登记不刮；进程重启、点「重置」、写标签改了大小与 mtime 都不会让
   老文件重新变成「新增」——指纹比对时会忽略修改时间（标签写入必然改它）。
2. **音乐库里是不是已经有了**（``LibraryIndex``）。库里有同名 / 文件名互为后缀 /
   整批顺序配对的音频就跳过，绝不重复刮进库。

另外两条保护：下载器临时文件（``.part`` / ``.!qB`` …）与还在写入的文件先不碰。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from musicpilot.adapters.scanner import scan_files
from musicpilot.config import RuntimeSettings
from musicpilot.domain.models import JobKind
from musicpilot.services.jobs import JobConflict, JobManager
from musicpilot.services.scraper import JobRequest
from musicpilot.services.settings_service import SettingsError, SettingsService

logger = logging.getLogger("musicpilot.watcher")

# 每次最多排这么多文件，避免一次性把上千个文件塞进一个任务
MAX_BATCH = 500
# 已处理指纹上限，与 Store._MAX_SEEN 对应
_MAX_SEEN = 200_000
# 音乐库索引上限，防止超大曲库每次扫描都拖慢轮询
_LIBRARY_SCAN_LIMIT = 200_000
# 下载器的临时文件后缀：还没写完，等它变成正式文件名再说
_PARTIAL_SUFFIXES = (
    ".part",
    ".partial",
    ".!qb",
    ".aria2",
    ".crdownload",
    ".download",
    ".tmp",
    ".temp",
    ".filepart",
)


@dataclass(frozen=True, slots=True)
class LibraryEntry:
    """One audio file already sitting in the music library."""

    path: str
    name: str
    size: int
    mtime: float


class LibraryIndex:
    """音乐库里已有的音频文件，用来判断「这个文件已经刮过了」。

    两种判定，都不看修改时间——写标签会改动源文件的 mtime，时间戳在这里没有意义：

    * **同名**：库里有同名文件。
    * **后缀**：源文件与库内文件名（去扩展名）互为后缀，
      ``01 - Bohemian Rhapsody.flac`` 对得上 ``01 - Queen - Bohemian Rhapsody.flac``。

    「整批顺序配对」只在容量恰好相等时成立，这个条件太脆（库里放几首别的歌就失效），
    也不再是对外承诺的判定方式，所以这里不再使用。
    """

    def __init__(self, entries: list[LibraryEntry]) -> None:
        self.entries = entries
        self.names = {entry.name for entry in entries}

    def holds(self, path: Path) -> bool:
        """库内是否已有对应文件；命中说明自动刮削不该再碰它。

        1. 同名文件：``01 - Queen - Bohemian Rhapsody.flac`` 这个名字已经在库里；
        2. 文件名互为后缀：命名模板改过标题（``01 - Bohemian Rhapsody.flac``），
           或反过来带上专辑歌手前缀。

        不看大小与时间戳：写标签会改动源文件，复制 / 硬链接 / 软链接模式下源文件
        又一直留在原地，靠尺寸猜「刚刮进来的那个」只会误判新歌。
        """
        name = path.name.lower()
        if name in self.names:
            return True
        stem = Path(name).stem
        for candidate in self.names:
            other = Path(candidate).stem
            if len(other) < 4 or not stem:
                continue
            if stem.endswith(other) or other.endswith(stem):
                return True
        return False


def _index_library(
    library: Path,
    extensions: frozenset[str],
    exclude_dirs: frozenset[str],
) -> list[LibraryEntry]:
    """Walk the music library once; only audio files are indexed."""
    entries: list[LibraryEntry] = []
    for dirpath, dirnames, filenames in os.walk(library, followlinks=False):
        dirnames[:] = [
            name for name in dirnames if name not in exclude_dirs and not name.startswith(".")
        ]
        for filename in filenames:
            if Path(filename).suffix.lower() not in extensions:
                continue
            full = Path(dirpath) / filename
            try:
                stat = full.stat()
            except OSError:
                continue
            entries.append(
                LibraryEntry(
                    path=str(full),
                    name=filename.lower(),
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                )
            )
            if len(entries) >= _LIBRARY_SCAN_LIMIT:
                return entries
    return entries


class AutoScraper:
    """Background loop; one instance per process, owned by the lifespan."""

    def __init__(self, jobs: JobManager, settings: SettingsService) -> None:
        self.jobs = jobs
        self.settings = settings
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._seen: dict[str, tuple[int, float]] = {}
        self._pending: dict[str, tuple[int, float]] = {}
        self._signature: tuple[str, ...] | None = None
        self._last_sync = 0.0
        self._loaded = False
        # 本轮待写库的指纹；攒着批量落盘，别每轮都开一次写入事务
        self._dirty: dict[str, tuple[int, float]] = {}
        self._dirty_prune = False
        # 下一轮扫描只记录存量文件、不排任务（首次开启或刚换了源目录）
        self._baseline_pending = True
        # 音乐库里已有的文件索引，用于判断「这个文件刮过了」
        self._library = LibraryIndex([])
        # 本轮刚排进任务的文件：任务的明细还没落库，这一两轮别重复判定
        self._running_paths: set[str] = set()
        # 指纹攒到这个数量再落盘，一次事务写完
        self.flush_threshold = 20

        self.running = False
        self.enabled = False
        self.scanning = False
        self.last_scan: float | None = None
        self.last_job_id: str | None = None
        self.last_error = ""
        self.detected = 0
        self.baselined = 0
        self.in_library = 0
        self.in_library_new = 0

    # ---- lifecycle ------------------------------------------------------
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="musicpilot-watcher")

    async def shutdown(self) -> None:
        self.flush()
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
        if runtime.watch_enabled:
            # 开启（或又点了一次开启）：存量文件先登记一遍，别一开就全量刮
            self._baseline_pending = True
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
            "baselined": self.baselined,
            "tracked": len(self._seen),
            "in_library": self.in_library,
            "in_library_new": self.in_library_new,
        }

    def mark_processed(self, paths: list[str]) -> None:
        """Treat these files as done so the watcher never picks them up again."""
        for value in paths:
            self._remember(value)
        self.flush()

    def release(self, paths: list[str]) -> None:
        """排任务失败时把指纹退回去，下一轮重新判定，别把文件默默丢掉。"""
        for value in paths:
            self._seen.pop(value, None)
            self._dirty.pop(value, None)

    def reset(self) -> None:
        """Forget everything seen: the next scan treats existing files as old ones again."""
        self._seen.clear()
        self._pending.clear()
        self._dirty.clear()
        self._dirty_prune = False
        self._signature = None
        self._baseline_pending = True
        self.baselined = 0
        self.detected = 0
        self._running_paths.clear()
        self._library = LibraryIndex([])
        try:
            self.jobs.store.clear_seen()
        except Exception as exc:  # noqa: BLE001 - resetting the UI must not crash
            self.last_error = f"清空已处理记录失败：{type(exc).__name__}: {exc}"
            logger.warning("清空 watch_seen 失败", exc_info=True)
        self.kick()

    def flush(self) -> None:
        """Persist buffered fingerprints; safe to call from the event loop."""
        if not self._dirty and not self._dirty_prune:
            return
        entries, self._dirty = self._dirty, {}
        prune, self._dirty_prune = self._dirty_prune, False
        try:
            if entries:
                self.jobs.store.save_seen(entries, prune=prune)
            elif prune:
                self.jobs.store.save_seen({}, prune=True)
        except Exception as exc:  # noqa: BLE001 - the loop must survive anything
            self.last_error = f"记录已处理文件失败：{type(exc).__name__}: {exc}"
            logger.warning("写入 watch_seen 失败", exc_info=True)

    # ---- loop -----------------------------------------------------------
    async def _loop(self) -> None:
        self.running = True
        while True:
            try:
                changed = self._sync_settings()
                if self.enabled:
                    self._load_seen()
                if self.enabled and not changed and self._settle_ready():
                    await self._tick()
                self._maybe_flush(self.flush_threshold)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the loop must survive anything
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("自动刮削循环出错")
            await self._sleep(self._interval())

    def _load_seen(self) -> None:
        """Read the persisted fingerprints once per process.

        必须在读设置之后调用：换了源目录时内存指纹已被抹掉，这时把库里的
        记录读回来，才不会把新目录里的存量文件当成新文件刮一遍。
        """
        if self._loaded:
            return
        self._loaded = True
        try:
            self._seen.update(self.jobs.store.load_seen())
        except Exception as exc:  # noqa: BLE001 - fall back to a memory-only run
            self.last_error = f"读取已处理记录失败：{type(exc).__name__}: {exc}"
            logger.warning("读取 watch_seen 失败", exc_info=True)

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

    def _sync_settings(self) -> bool:
        """Mirror settings into the loop; True when the source root just changed."""
        runtime = self.settings.get()
        signature = (
            str(runtime.source_root),
            str(runtime.library_root),
            "watch" if runtime.watch_enabled else "off",
        )
        self.enabled = runtime.watch_enabled
        if self._signature == signature:
            return False
        previous, self._signature = self._signature, signature
        self._last_sync = time.time()
        if previous is None:
            return False
        # 换源目录或重新开启监控：抹掉内存指纹，下一轮把存量文件当旧文件记下来
        self._seen.clear()
        self._pending.clear()
        self._baseline_pending = True
        self.baselined = 0
        self.kick()
        return True

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
            baselining = self._baseline_pending
            if not found:
                self._baseline_pending = False
            candidates = [
                audio
                for audio in found
                if not self._partial(audio) and not self._unstable(audio)
            ]
            self._forget_missing({str(audio) for audio in found})
            await self._sync_library()

            fresh: list[Path] = []
            for audio in candidates:
                path = str(audio)
                fingerprint = self._fingerprint(path)
                if fingerprint is None or fingerprint[0] < 0:
                    continue
                if path in self._running_paths and self._in_library(audio):
                    # 刚排进任务、库里也已经有了：这条不再重复判定
                    self._pending.pop(path, None)
                    self._running_paths.discard(path)
                    self._remember(path, fingerprint)
                    continue
                recorded = self._seen.get(path)
                if baselining:
                    # 刚开启 / 刚换目录：存量文件只记不刮，老文件不碰
                    self._remember(path, fingerprint)
                    self.baselined += 1
                    continue
                new_file = not self._same(recorded, fingerprint)
                if not new_file:
                    # 源目录里的老文件：不论库里有没有，都不再自动刮
                    self._pending.pop(path, None)
                    if fingerprint[0] != recorded[0]:  # type: ignore[index]
                        self._remember(path, fingerprint)
                    continue
                if self._in_library(audio):
                    # 源目录里是新增的，但音乐库已经有了：算处理过，跳过
                    self._pending.pop(path, None)
                    self.in_library_new += 1
                    self._remember(path, fingerprint)
                    continue
                previous = self._pending.get(path)
                if previous is None or previous[0] != fingerprint[0]:
                    # 大小还在变：下一轮再确认一次，别抢着刮半成品
                    self._pending[path] = fingerprint
                    continue
                self._pending.pop(path, None)
                fresh.append(audio)
            self._baseline_pending = False
            self.last_scan = time.time()
            self.last_error = ""
            if not fresh:
                self._maybe_flush()
                return
            batch = sorted(fresh)[:MAX_BATCH]
            paths = [str(path) for path in batch]
            self.mark_processed(paths)
            try:
                job = self.jobs.start(
                    JobRequest(kind=JobKind.AUTO, paths=paths),
                    title=batch[0].name if len(batch) == 1 else "",
                )
            except BaseException:
                self.release(paths)
                raise
            self.last_job_id = job.id
            self.detected += len(batch)
            self._running_paths = set(paths)
            logger.info("自动刮削：已排入 %s 个新文件，任务 %s", len(batch), job.id)
            self._maybe_flush()
        except JobConflict:
            return  # 手动任务在跑，下一轮再看
        except SettingsError as exc:
            self.last_error = str(exc)
        finally:
            self.scanning = False

    async def _sync_library(self) -> None:
        """Index the music library so "already scraped" survives restarts.

        只看音乐库、不看时间戳：写标签会改动源文件的 mtime，而复制 / 硬链接 /
        软链接模式下源文件又一直留在原地，指纹永远对不上它已经进过库这件事。
        """
        runtime = self.settings.get()
        library = runtime.library_root
        if not library.is_dir():
            self._library = LibraryIndex([])
            self.in_library = 0
            return
        # 每轮都重新索引：库很小，一次遍历很便宜，而缓存会让「刚刮进来的文件」看不到
        try:
            entries = await asyncio.to_thread(
                _index_library,
                library,
                self.jobs.scraper.base.extensions,
                self.jobs.scraper.base.excluded_directories,
            )
        except OSError as exc:
            self.last_error = f"读取音乐库失败：{type(exc).__name__}: {exc}"
            return
        self._library = LibraryIndex(entries)
        self.in_library = len(entries)

    def _in_library(self, path: Path) -> bool:
        """True when the music library already holds this recording."""
        return self._library.holds(path)

    def _forget_missing(self, present: set[str]) -> None:
        """源目录里已经没有的文件（被移动 / 删除）不用再记，表才不会越攒越大。"""
        if not self._seen:
            return
        missing = [key for key in self._seen if key not in present]
        if not missing:
            return
        for path in missing:
            self._seen.pop(path, None)
            self._dirty.pop(path, None)
            self._pending.pop(path, None)
            self._running_paths.discard(path)
        try:
            self.jobs.store.forget_seen(missing)
        except Exception as exc:  # noqa: BLE001 - 清理失败只是表大一点，不影响刮削
            logger.debug("清理 watch_seen 失败：%s", exc)

    def _maybe_flush(self, threshold: int = 50) -> None:
        """Write buffered fingerprints once they are worth a transaction."""
        if len(self._dirty) >= threshold or (self._dirty and self._dirty_prune):
            self.flush()

    @staticmethod
    def _partial(path: Path) -> bool:
        """Downloader leftovers: the real file may still be on its way."""
        name = path.name.lower()
        return any(name.endswith(suffix) for suffix in _PARTIAL_SUFFIXES)

    @staticmethod
    def _size_of(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    @staticmethod
    def _fingerprint(path: str) -> tuple[int, float] | None:
        """``(大小, 修改时间)``；``size == -1`` 表示文件已经不在源目录了。"""
        try:
            stat = Path(path).stat()
        except OSError:
            return -1, 0.0
        return stat.st_size, round(stat.st_mtime, 3)

    @staticmethod
    def _same(recorded: tuple[int, float] | None, current: tuple[int, float] | None) -> bool:
        """登记的指纹与当前是否还是同一个文件。

        只比文件大小，不比修改时间：写标签一定会改 mtime，而源文件在被刮之前就
        被写了标签——比时间戳的话，每轮轮询都会把它当「刚出现的新文件」再刮一遍。
        """
        if recorded is None or current is None:
            return False
        if recorded[0] == -1 or current[0] == -1:
            return recorded[0] == current[0]
        return recorded[0] == current[0]

    def _unstable(self, path: Path) -> bool:
        """A file still being written/downloaded gets another chance next round."""
        try:
            stat = path.stat()
        except OSError:
            return True
        settle = self.settings.get().watch_settle
        return settle > 0 and time.time() - stat.st_mtime < settle

    def _remember(self, path: str, fingerprint: tuple[int, float] | None = None) -> None:
        value = fingerprint or self._fingerprint(path) or (0, 0.0)
        self._seen[path] = value
        self._dirty[path] = value
        if len(self._seen) > _MAX_SEEN:
            for key in list(self._seen)[: _MAX_SEEN // 10]:
                self._seen.pop(key, None)
            self._dirty_prune = True
