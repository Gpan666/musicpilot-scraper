"""音乐库的纯规则：缺什么字段、候选匹配度几分（1–10）、补全时补什么。

界面上的「匹配度」就是这里的 ``match_score``：0–1 的相似度换算成 1–10 的整数，
1 = 几乎不沾边，10 = 标题、歌手、专辑全对上。规则都在这里算，路由与服务只负责搬运。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from musicpilot.domain.matcher import score_candidate
from musicpilot.domain.models import TrackMetadata

# 「查漏补缺」关心的字段；缺任何一个就算待补全。
COVER_FIELDS = ("title", "artist", "album", "album_artist", "year", "track_number")

FIELD_LABELS = {
    "title": "标题",
    "artist": "歌手",
    "album": "专辑",
    "album_artist": "专辑艺术家",
    "year": "年份",
    "track_number": "专辑轨号",
    "disc_number": "碟号",
    "lyrics": "歌词",
    "cover": "封面",
}

# 补全时只补这些字段，并且只补空的：已有的标签一个都不覆盖。
FILLABLE = ("artist", "album", "album_artist", "year", "track_number", "disc_number")


def missing_fields(meta: TrackMetadata, *, has_cover: bool = True) -> list[str]:
    missing = [name for name in COVER_FIELDS if not getattr(meta, name)]
    if not has_cover:
        missing.append("cover")
    return missing


def needs_lyrics(meta: TrackMetadata) -> bool:
    return not (meta.lyrics or "").strip()


def missing_labels(fields: Iterable[str]) -> str:
    return "、".join(FIELD_LABELS.get(name, name) for name in fields)


def match_score(query: TrackMetadata, candidate: TrackMetadata) -> int:
    """0–1 的相似度 → 1–10 的整数匹配度；完全对不上也不会掉到 0。"""
    return max(1, min(10, round(score_candidate(query, candidate) * 10)))


def fill_from(local: TrackMetadata, remote: TrackMetadata, *, with_lyrics: bool = True) -> TrackMetadata:
    """只补空字段，绝不覆盖文件里已有的值。

    标题缺失时也可以从候选补，否则「未命名」的文件永远补不上。
    """
    updates: dict[str, object] = {}
    if not local.title.strip() and remote.title.strip():
        updates["title"] = remote.title
    for name in FILLABLE:
        if not getattr(local, name) and getattr(remote, name):
            updates[name] = getattr(remote, name)
    if not local.album_artist and not updates.get("album_artist"):
        fallback = updates.get("artist") or local.artist or remote.artist
        if fallback:
            updates["album_artist"] = fallback
    if with_lyrics and not (local.lyrics or "").strip() and remote.lyrics:
        updates["lyrics"] = remote.lyrics
    if not local.cover_url and remote.cover_url:
        updates["cover_url"] = remote.cover_url
    if not updates:
        return local
    return replace(local, **updates)  # type: ignore[arg-type]
