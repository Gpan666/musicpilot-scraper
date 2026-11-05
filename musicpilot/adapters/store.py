"""Small SQLite persistence layer (standard library only).

A fresh connection per call keeps the store safe with FastAPI's thread pool and
the background worker without a global lock around the connection itself.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from musicpilot.domain.models import JobSnapshot, PlanItem

_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    total INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0,
    finished_at REAL,
    message TEXT NOT NULL DEFAULT '',
    dry_run INTEGER NOT NULL DEFAULT 0,
    current_file TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    options TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS job_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    source TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    source_name TEXT NOT NULL DEFAULT 'local',
    title TEXT NOT NULL DEFAULT '',
    artist TEXT,
    album TEXT,
    year INTEGER,
    track_number INTEGER,
    detail TEXT NOT NULL DEFAULT '',
    link_target TEXT,
    local_title TEXT NOT NULL DEFAULT '',
    local_artist TEXT,
    local_album TEXT,
    local_year INTEGER,
    local_track_number INTEGER,
    cover_url TEXT,
    candidates TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_items_job ON job_items(job_id, id);
CREATE TABLE IF NOT EXISTS watch_seen (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    seen_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS metadata_cache (
    key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
"""

# 已处理指纹上限，防止 watch_seen 无限增长
_MAX_SEEN = 200_000

def _add_columns(
    connection: sqlite3.Connection, table: str, needed: dict[str, str]
) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    for name, ddl in needed.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


class Store:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        if self._initialized:
            return
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(_SCHEMA)
            self._migrate(connection)
        self._initialized = True

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        """Additive migrations for databases created by earlier versions."""
        _add_columns(
            connection,
            "job_items",
            {
                "local_title": "TEXT NOT NULL DEFAULT ''",
                "local_artist": "TEXT",
                "local_album": "TEXT",
                "local_year": "INTEGER",
                "local_track_number": "INTEGER",
                "cover_url": "TEXT",
                "candidates": "TEXT NOT NULL DEFAULT '[]'",
            },
        )
        _add_columns(connection, "jobs", {"title": "TEXT NOT NULL DEFAULT ''"})

    # ---- settings -------------------------------------------------------
    def get_overrides(self) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute("SELECT key, value FROM app_settings").fetchall()
        result: dict[str, Any] = {}
        for row in rows:
            try:
                result[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                continue
        return result

    def replace_overrides(self, values: dict[str, Any]) -> None:
        self.initialize()
        with self._connect() as connection:
            connection.execute("DELETE FROM app_settings")
            connection.executemany(
                "INSERT INTO app_settings(key, value) VALUES (?, ?)",
                [(key, json.dumps(value, ensure_ascii=False)) for key, value in values.items()],
            )

    # ---- jobs -----------------------------------------------------------
    def save_job(self, job: JobSnapshot, options: dict[str, Any] | None = None) -> None:
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    id, kind, status, total, done, failed, skipped, created_at,
                    processed, finished_at, message, dry_run, current_file, title, options
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status, total=excluded.total, done=excluded.done,
                    failed=excluded.failed, skipped=excluded.skipped,
                    processed=excluded.processed, finished_at=excluded.finished_at,
                    message=excluded.message, current_file=excluded.current_file,
                    title=excluded.title
                """,
                (
                    job.id,
                    job.kind,
                    job.status,
                    job.total,
                    job.done,
                    job.failed,
                    job.skipped,
                    job.created_at,
                    job.processed,
                    job.finished_at,
                    job.message,
                    int(job.dry_run),
                    job.current_file,
                    job.title,
                    json.dumps(options or {}, ensure_ascii=False),
                ),
            )

    def update_progress(self, job_id: str, **fields: Any) -> None:
        allowed = {
            "status",
            "total",
            "done",
            "failed",
            "skipped",
            "processed",
            "finished_at",
            "message",
            "current_file",
            "title",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        clause = ", ".join(f"{key}=?" for key in updates)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE jobs SET {clause} WHERE id=?",  # noqa: S608 - keys are whitelisted
                (*updates.values(), job_id),
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_jobs(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def active_job(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE status IN ('pending','running') "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    # ---- job items ------------------------------------------------------
    def add_items(self, job_id: str, items: Iterable[PlanItem]) -> None:
        rows = []
        now = time.time()
        for item in items:
            rows.append(
                (
                    job_id,
                    item.source,
                    item.target,
                    item.mode,
                    item.status,
                    item.confidence,
                    item.source_name,
                    item.title,
                    item.artist,
                    item.album,
                    item.year,
                    item.track_number,
                    item.detail,
                    item.link_target,
                    item.local_title,
                    item.local_artist,
                    item.local_album,
                    item.local_year,
                    item.local_track_number,
                    item.cover_url,
                    json.dumps(item.candidates, ensure_ascii=False),
                    now,
                )
            )
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO job_items (
                    job_id, source, target, mode, status, confidence, source_name,
                    title, artist, album, year, track_number, detail, link_target,
                    local_title, local_artist, local_album, local_year,
                    local_track_number, cover_url, candidates, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def list_items(self, job_id: str, *, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM job_items WHERE job_id=? ORDER BY id LIMIT ? OFFSET ?",
                (job_id, limit, offset),
            ).fetchall()
        return [self._decode_item(dict(row)) for row in rows]

    @staticmethod
    def _decode_item(row: dict[str, Any]) -> dict[str, Any]:
        raw = row.get("candidates")
        try:
            row["candidates"] = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except json.JSONDecodeError:
            row["candidates"] = []
        return row

    def item_counts(self, job_id: str) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS n FROM job_items WHERE job_id=? GROUP BY status",
                (job_id,),
            ).fetchall()
        return {row["status"]: row["n"] for row in rows}

    def recent_done(self, limit: int = 8) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT source, target, mode, status, created_at FROM job_items "
                "WHERE status='done' ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ---- watcher fingerprint -------------------------------------------
    def load_seen(self, limit: int = _MAX_SEEN) -> dict[str, tuple[int, float]]:
        """Recent fingerprints, newest first, so a restart never rescrapes old files."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT path, size, mtime FROM watch_seen ORDER BY seen_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return {row["path"]: (row["size"], row["mtime"]) for row in rows}

    def save_seen(
        self,
        entries: dict[str, tuple[int, float]],
        *,
        prune: bool = False,
        limit: int = _MAX_SEEN,
    ) -> None:
        if not entries:
            return
        self.initialize()
        now = time.time()
        with self._connect() as connection:
            connection.executemany(
                "INSERT INTO watch_seen(path, size, mtime, seen_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET "
                "size=excluded.size, mtime=excluded.mtime, seen_at=excluded.seen_at",
                [(path, size, mtime, now) for path, (size, mtime) in entries.items()],
            )
            if prune:
                self._prune_seen(connection, limit)

    def clear_seen(self) -> None:
        self.initialize()
        with self._connect() as connection:
            connection.execute("DELETE FROM watch_seen")

    @staticmethod
    def _prune_seen(connection: sqlite3.Connection, limit: int) -> None:
        total = connection.execute("SELECT COUNT(*) AS n FROM watch_seen").fetchone()["n"]
        if total > limit:
            connection.execute(
                "DELETE FROM watch_seen WHERE path IN ("
                "SELECT path FROM watch_seen ORDER BY seen_at ASC LIMIT ?)",
                (total - limit,),
            )

    # ---- users ----------------------------------------------------------
    def user_count(self) -> int:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"])

    def get_user(self, username: str) -> dict[str, Any] | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username=?", (username,)
            ).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, username, is_admin, created_at FROM users ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def create_user(
        self, username: str, password_hash: str, *, is_admin: bool = False
    ) -> dict[str, Any]:
        """Insert a user; raises sqlite3.IntegrityError when the name is taken."""
        self.initialize()
        now = time.time()
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO users(username, password_hash, is_admin, created_at) "
                "VALUES (?, ?, ?, ?)",
                (username, password_hash, int(is_admin), now),
            )
        return {
            "id": cursor.lastrowid,
            "username": username,
            "is_admin": is_admin,
            "created_at": now,
        }

    def delete_user(self, username: str) -> bool:
        """Only used by tests/admin tooling; never removes the last admin."""
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT is_admin FROM users WHERE username=?", (username,)
            ).fetchone()
            if row is None:
                return False
            if row["is_admin"]:
                remaining = connection.execute(
                    "SELECT COUNT(*) AS n FROM users WHERE is_admin=1"
                ).fetchone()["n"]
                if remaining <= 1:
                    return False
            connection.execute("DELETE FROM users WHERE username=?", (username,))
        return True

    # ---- metadata cache -------------------------------------------------
    def cache_get(self, key: str, *, max_age: float = 7 * 24 * 3600) -> Any | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload, created_at FROM metadata_cache WHERE key=?", (key,)
            ).fetchone()
        if row is None or time.time() - row["created_at"] > max_age:
            return None
        try:
            return json.loads(row["payload"])
        except json.JSONDecodeError:
            return None

    def cache_set(self, key: str, payload: Any) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO metadata_cache(key, payload, created_at) VALUES (?, ?, ?)",
                (key, json.dumps(payload, ensure_ascii=False), time.time()),
            )
