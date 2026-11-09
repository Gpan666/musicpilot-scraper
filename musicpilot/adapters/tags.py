"""Write completed metadata back into the audio file, then the file is transferred.

Because tags are written *before* the transfer, ``hardlink`` and ``symlink``
modes necessarily modify the source file too. This is intentional and is
documented in README.md and in MODE_INFO.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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


# MP4 / ID3 只认 jpeg、png；webp 能预览但不内嵌（_write_cover_sync 会自己拒掉）
_MIME_ALIASES = {"image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg"}
_COVER_REFERERS = {
    "music.126.net": "https://music.163.com/",
    "kugou.com": "https://www.kugou.com/",
    "kuwo.cn": "https://www.kuwo.cn/",
    "kwcdn.com": "https://www.kuwo.cn/",
    "gtimg.cn": "https://y.qq.com/",
    "qq.com": "https://y.qq.com/",
}


def _referer_for(url: str) -> str | None:
    """各家图床基本都认 Referer；按 host 猜一个origin，别拿 A 站的 Referer 求 B 站的图。"""
    host = (urlparse(url).hostname or "").lower()
    for suffix, referer in _COVER_REFERERS.items():
        if host.endswith(suffix):
            return referer
    return None


def _sniff_mime(data: bytes) -> str:
    """以文件头为准：不少图床把 jpg 回成 octet-stream 甚至 text/plain。"""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"GIF8"):
        return "image/gif"
    return ""


def _normalize_mime(declared: str, data: bytes) -> str | None:
    """把声明的 Content-Type 与文件头对一遍；认不出是真图就返回 None。"""
    mime = (declared or "").split(";", 1)[0].strip().lower()
    mime = _MIME_ALIASES.get(mime, mime)
    sniffed = _sniff_mime(data)
    if mime.startswith("image/"):
        # 声明是 image/* 但文件头认得出，就以文件头为准（image/jpg 之类写法各家不一）
        return sniffed or mime
    return sniffed or None


async def fetch_cover(
    url: str | None,
    *,
    client: httpx.AsyncClient,
) -> tuple[bytes, str] | None:
    """抓一张封面回来。

    以前只认写对的 Content-Type，图床回 ``image/jpg`` / ``application/octet-stream``
    就被当成「不是图」丢掉——这正是「封面刮削不行」的直接原因之一。现在:
    文件头优先，声明的 MIME 只做参考；拿不到图片或不认识的格式一律返回 None。
    """
    if not url:
        return None
    target = url.strip()
    if target.startswith("//"):
        target = f"https:{target}"
    if not target.lower().startswith(("http://", "https://")):
        return None
    headers: dict[str, str] = {"Accept": "image/*,*/*;q=0.8"}
    referer = _referer_for(target)
    if referer:
        headers["Referer"] = referer
    try:
        response = await client.get(target, follow_redirects=True, headers=headers)
        response.raise_for_status()
    except Exception:  # noqa: BLE001 - cover retrieval is best effort
        return None
    data = response.content
    if not data or len(data) > _MAX_COVER_BYTES:
        return None
    mime = _normalize_mime(response.headers.get("content-type", ""), data)
    if mime is None:
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


# ---------------------------------------------------------------- 音乐库补全
# 音乐库的「查漏补缺」只改标签：不动文件位置，不复制，不建硬链接/软链接。
# 下面这条窄路只做一件事——把指定字段写进「空着的槽位」，已有的值一个都不覆盖。

_MISSING_FIELDS = frozenset(
    {"title", "artist", "album", "album_artist", "year", "track_number", "disc_number", "lyrics"}
)
_EASY_KEYS = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "album_artist": "albumartist",
    "year": "date",
    "track_number": "tracknumber",
    "disc_number": "discnumber",
}


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    items = value if isinstance(value, (list, tuple)) else [value]
    for item in items:
        text = getattr(item, "text", item)
        if isinstance(text, (list, tuple)):
            text = text[0] if text else ""
        if text is not None and str(text).strip():
            return True
    return False


def _existing(audio: Any, key: str) -> bool:
    try:
        return _has_value(audio.get(key))
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def _write_lyrics_sync(path: Path, text: str) -> bool:
    """歌词没有 easy 键，得按后端各自的写法来：ID3 用 USLT，Vorbis 用 lyrics。"""
    try:
        audio = MutagenFile(str(path))
    except Exception:  # noqa: BLE001
        return False
    if audio is None:
        return False
    try:
        if type(audio).__name__ == "MP3" or type(getattr(audio, "tags", None)).__name__.startswith("ID3"):
            from mutagen.id3 import USLT

            if audio.tags is None:
                audio.add_tags()
            audio.tags.delall("USLT")
            audio.tags.add(USLT(encoding=3, lang="chi", desc="", text=text))
        else:
            audio["lyrics"] = [text]
        audio.save()
        return True
    except Exception:  # noqa: BLE001 - 歌词写不进去不影响其它字段
        return False
    finally:
        _close(audio)


def write_missing_tags_sync(
    path: Path, wanted: dict[str, Any], *, write_lyrics: bool = False
) -> list[str]:
    """只写文件里还没有的字段，返回真正写进去的字段名。

    ``wanted`` 只认 title/artist/album/album_artist/year/track_number/
    disc_number，其余键忽略。写失败一律当「没写」，不会为了写标签去碰文件位置。
    """
    wanted = {k: v for k, v in wanted.items() if k in _MISSING_FIELDS and v not in (None, "")}
    if not wanted:
        return []
    try:
        audio = MutagenFile(str(path), easy=True)
    except Exception:  # noqa: BLE001 - 坏文件不该让整批任务失败
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
        for name, value in wanted.items():
            if name == "lyrics":
                continue
            key = _EASY_KEYS[name]
            if _existing(audio, key):
                continue
            try:
                audio[key] = [str(value)]
                written.append(name)
            except (KeyError, TypeError, ValueError):
                continue
        if written:
            audio.save()
    except Exception:  # noqa: BLE001 - 写标签是尽力而为
        return []
    finally:
        _close(audio)
    lyrics = str(wanted.get("lyrics") or "").strip()
    if lyrics and write_lyrics and _write_lyrics_sync(path, lyrics):
        written.append("lyrics")
    return written


async def write_missing_tags(
    path: Path,
    wanted: dict[str, Any],
    *,
    write_lyrics: bool = False,
    cover: tuple[bytes, str] | None = None,
) -> list[str]:
    """只补空字段；``cover`` 传了才写封面，且只在文件本来没有封面时写。"""
    written = await asyncio.to_thread(
        write_missing_tags_sync, path, wanted, write_lyrics=write_lyrics
    )
    if cover is not None and "cover" not in written:
        if await asyncio.to_thread(_write_cover_if_missing_sync, path, cover):
            written.append("cover")
    return written


def _write_cover_if_missing_sync(path: Path, cover: tuple[bytes, str]) -> bool:
    """文件里没有封面才写：已有封面的一律不覆盖，理由和标签一样。"""
    from musicpilot.adapters.scanner import embedded_cover

    if embedded_cover(path) is not None:
        return False
    return _write_cover_sync(path, cover)
