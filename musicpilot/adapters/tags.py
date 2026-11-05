"""Write completed metadata back into the audio file, then the file is transferred.

Because tags are written *before* the transfer, ``hardlink`` and ``symlink``
modes necessarily modify the source file too. This is intentional and is
documented in README.md and in MODE_INFO.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
from mutagen import File as MutagenFile

from musicpilot.domain.models import TrackMetadata

_MAX_COVER_BYTES = 10 * 1024 * 1024
_MAX_TRACK_NUMBER = 99


def _close(audio: Any) -> None:
    close = getattr(audio, "close", None)
    if callable(close):
        close()


def _set(audio: Any, key: str, value: str | int | None) -> bool:
    if value is None or str(value) == "":
        return False
    try:
        audio[key] = [str(value)]
        return True
    except (KeyError, TypeError, ValueError):
        return False


def _write_basic_sync(
    path: Path,
    meta: TrackMetadata,
    lyrics_enabled: bool,
    track_number: int | None = None,
) -> list[str]:
    try:
        audio = MutagenFile(str(path), easy=True)
    except Exception:  # noqa: BLE001
        return []
    if audio is None:
        return []
    written: list[str] = []
    try:
        if audio.tags is None:
            try:
                audio.add_tags()
            except Exception:  # noqa: BLE001
                return []
        fields = (
            ("title", meta.title),
            ("artist", meta.artist),
            ("album", meta.album),
            ("albumartist", meta.album_artist or meta.artist),
            ("date", meta.year),
            ("tracknumber", track_number if track_number is not None else meta.track_number),
            ("discnumber", meta.disc_number),
        )
        for key, value in fields:
            if _set(audio, key, value):
                written.append(key)
        if lyrics_enabled and meta.lyrics:
            if _set(audio, "lyrics", meta.lyrics):
                written.append("lyrics")
        audio.save()
    finally:
        _close(audio)
    return written


def _write_cover_sync(path: Path, cover: tuple[bytes, str] | None) -> bool:
    if cover is None:
        return False
    data, mime = cover
    try:
        audio = MutagenFile(str(path))
    except Exception:  # noqa: BLE001
        return False
    if audio is None:
        return False
    try:
        class_name = type(audio).__name__
        if class_name == "MP3":
            from mutagen.id3 import APIC

            if audio.tags is None:
                audio.add_tags()
            audio.tags.delall("APIC")
            audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
        elif class_name == "FLAC":
            from mutagen.flac import Picture

            picture = Picture()
            picture.type = 3
            picture.mime = mime
            picture.desc = "Cover"
            picture.data = data
            audio.clear_pictures()
            audio.add_picture(picture)
        elif class_name in {"MP4", "M4A"}:
            from mutagen.mp4 import MP4Cover

            if mime not in {"image/jpeg", "image/png"}:
                return False
            fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
            audio["covr"] = [MP4Cover(data, imageformat=fmt)]
        elif class_name in {"OggVorbis", "OggOpus"}:
            import base64

            from mutagen.flac import Picture

            picture = Picture()
            picture.type = 3
            picture.mime = mime
            picture.desc = "Cover"
            picture.data = data
            audio["metadata_block_picture"] = [
                base64.b64encode(picture.write()).decode("ascii")
            ]
        else:
            return False
        audio.save()
        return True
    except Exception:  # noqa: BLE001 - cover writing is best effort
        return False
    finally:
        _close(audio)


def write_tags_sync(
    path: Path,
    meta: TrackMetadata,
    *,
    cover: tuple[bytes, str] | None = None,
    lyrics_enabled: bool = False,
    track_number: int | None = None,
) -> list[str]:
    written = _write_basic_sync(path, meta, lyrics_enabled, track_number)
    if _write_cover_sync(path, cover):
        written.append("cover")
    return written


async def fetch_cover(
    url: str | None,
    *,
    client: httpx.AsyncClient,
) -> tuple[bytes, str] | None:
    if not url:
        return None
    try:
        response = await client.get(url, follow_redirects=True)
        response.raise_for_status()
    except Exception:  # noqa: BLE001 - cover retrieval is best effort
        return None
    data = response.content
    if not data or len(data) > _MAX_COVER_BYTES:
        return None
    mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if mime not in {"image/jpeg", "image/png", "image/webp"}:
        if data.startswith(b"\xff\xd8"):
            mime = "image/jpeg"
        elif data.startswith(b"\x89PNG"):
            mime = "image/png"
        elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            mime = "image/webp"
        else:
            return None
    return data, mime


async def apply_tags(
    path: Path,
    meta: TrackMetadata,
    *,
    cover: tuple[bytes, str] | None = None,
    lyrics_enabled: bool = False,
    track_number: int | None = None,
) -> list[str]:
    return await asyncio.to_thread(
        write_tags_sync,
        path,
        meta,
        cover=cover,
        lyrics_enabled=lyrics_enabled,
        track_number=track_number,
    )
