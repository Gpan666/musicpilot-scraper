"""Environment configuration and the runtime (UI-editable) settings layer.

Priority for a runtime setting: SQLite override -> environment variable -> default.
Only the fields of :class:`RuntimeSettings` can be changed from the Web UI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from musicpilot.domain.transfer import ConflictPolicy, TransferMode


class Settings(BaseSettings):
    """Static deployment settings, read once at process start."""

    model_config = SettingsConfigDict(
        env_prefix="MP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "MusicPilot Scraper"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    data_dir: Path = Path("/data")
    source_dir: Path = Path("/media/downloads")
    library_dir: Path = Path("/media/music")
    static_dir: Path = Path(__file__).resolve().parent.parent / "web"

    admin_username: str = "admin"
    admin_password: str = ""
    session_secret: str = ""

    transfer_mode: TransferMode = TransferMode.COPY
    naming_template: str = "{artist}/{album}/{track:02d} - {title}"
    conflict_policy: ConflictPolicy = ConflictPolicy.RENAME
    symlink_relative: bool = True
    write_tags: bool = True
    embed_cover: bool = True
    write_lyrics: bool = False
    metadata_sources: str = "local,musicbrainz,netease"
    confidence_threshold: float = 0.62
    unknown_placeholder: str = "Unknown"

    musicbrainz_user_agent: str = "MusicPilot-Scraper/2.0 (self-hosted personal use)"
    http_proxy: str = ""
    request_timeout: float = 20.0

    browsable_roots: str = "/media"
    scan_extensions: str = ".mp3,.flac,.wav,.m4a,.aac,.ogg,.opus,.aiff,.aif,.dsf,.dff,.wma,.ape"
    exclude_dirs: str = "@eaDir,.git,.thumbnails,System Volume Information"
    max_scan_files: int = 5000
    scan_concurrency: int = 4
    job_concurrency: int = 2

    watch_enabled: bool = False
    watch_interval: float = 30.0
    watch_settle: float = 10.0

    @property
    def database_path(self) -> Path:
        return self.data_dir / "musicpilot.db"

    @property
    def secret_path(self) -> Path:
        return self.data_dir / "session.secret"

    @property
    def extensions(self) -> frozenset[str]:
        return frozenset(
            item.strip().lower()
            for item in self.scan_extensions.split(",")
            if item.strip()
        )

    @property
    def excluded_directories(self) -> frozenset[str]:
        return frozenset(
            item.strip() for item in self.exclude_dirs.split(",") if item.strip()
        )

    @property
    def browsable_paths(self) -> tuple[Path, ...]:
        return tuple(
            Path(item.strip())
            for item in self.browsable_roots.split(",")
            if item.strip()
        )

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                item.strip().lower()
                for item in self.metadata_sources.split(",")
                if item.strip()
            )
        )

    def runtime_defaults(self) -> dict[str, object]:
        return {
            "source_dir": str(self.source_dir),
            "library_dir": str(self.library_dir),
            "transfer_mode": self.transfer_mode.value,
            "naming_template": self.naming_template,
            "conflict_policy": self.conflict_policy.value,
            "symlink_relative": self.symlink_relative,
            "write_tags": self.write_tags,
            "embed_cover": self.embed_cover,
            "write_lyrics": self.write_lyrics,
            "metadata_sources": list(self.sources),
            "confidence_threshold": self.confidence_threshold,
            "unknown_placeholder": self.unknown_placeholder,
            "watch_enabled": self.watch_enabled,
            "watch_interval": self.watch_interval,
            "watch_settle": self.watch_settle,
        }


class RuntimeSettings(BaseModel):
    """Settings that can be inspected and changed while the service is running."""

    source_dir: str = Field(min_length=1)
    library_dir: str = Field(min_length=1)
    transfer_mode: TransferMode = TransferMode.COPY
    naming_template: str = Field(min_length=1, max_length=300)
    conflict_policy: ConflictPolicy = ConflictPolicy.RENAME
    symlink_relative: bool = True
    write_tags: bool = True
    embed_cover: bool = True
    write_lyrics: bool = False
    metadata_sources: list[Literal["local", "musicbrainz", "netease"]] = Field(
        default_factory=lambda: ["local", "musicbrainz", "netease"]
    )
    confidence_threshold: float = Field(default=0.62, ge=0.0, le=1.0)
    unknown_placeholder: str = Field(default="Unknown", min_length=1, max_length=40)
    # 自动刮削：轮询源目录，发现新文件就自动开一个刮削任务
    watch_enabled: bool = False
    watch_interval: float = Field(default=30.0, ge=5.0, le=3600.0)
    watch_settle: float = Field(default=10.0, ge=0.0, le=3600.0)

    @property
    def source_root(self) -> Path:
        return Path(self.source_dir)

    @property
    def library_root(self) -> Path:
        return Path(self.library_dir)


def load_settings() -> Settings:
    return Settings()
