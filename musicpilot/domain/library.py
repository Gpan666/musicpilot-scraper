"""音乐库的纯规则：缺什么字段、候选匹配度几分（1–10）、补全时补什么。

界面上的「匹配度」就是这里的 ``match_score``：0–1 的相似度换算成 1–10 的整数，
1 = 几乎不沾边，10 = 标题、歌手、专辑全对上。规则都在这里算，路由与服务只负责搬运。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from musicpilot.domain.matcher import score_candidate
from musicpilot.domain.models import ALBUM_REQUIRED_FIELDS, TrackKind, TrackMetadata

# 「查漏补缺」关心的字段：单曲只要求标题与歌手，专辑曲目六项照旧（见 required_fields）。
COVER_FIELDS = ALBUM_REQUIRED_FIELDS

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
    """缺什么字段：单曲只查标题 / 歌手，专辑曲目六项照旧。

    单曲本来就没有专辑与专辑轨号，拿专辑的规矩去量它，整库都会变成「缺 专辑轨号」。
    """
    missing = [name for name in meta.required_fields() if not getattr(meta, name)]
    if not has_cover:
        missing.append("cover")
    return missing


def plan_fill(
    local: TrackMetadata, remote: TrackMetadata, *, with_lyrics: bool = True
) -> dict[str, object]:
    """这次补全会写哪些字段、写成什么值：只补空字段，绝不覆盖。

    与 ``fill_from`` 同源，界面上的「将写入的字段清单」与实际落盘的一字不差——
    预览说会写，就一定写；预览说不会写（已有值 / 单曲不要求），就一个都不碰。
    """
    merged = fill_from(local, remote, with_lyrics=with_lyrics)
    planned: dict[str, object] = {}
    for name in (
        "title",
        "artist",
        "album",
        "album_artist",
        "year",
        "track_number",
        "disc_number",
    ):
        after, before = getattr(merged, name), getattr(local, name)
        if after and after != before:
            planned[name] = after
    if with_lyrics and not (local.lyrics or "").strip() and merged.lyrics:
        planned["lyrics"] = merged.lyrics
    return planned


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


# 单曲 / 专辑轨号：目录里只有这首歌，还是和别的歌同住一个目录？
# 「单曲」判定按下面的优先级来，判不准一律按专辑曲目处理——宁可多补，也不漏补。
_SINGLE_TAG_HINTS = ("single",)


def classify_kind(
    meta: TrackMetadata,
    *,
    sibling_audio_count: int | None = None,
    album_type: str | None = None,
) -> tuple[str, str]:
    """返回 ``(kind, kind_reason)``，reason 是一句能直接摆在界面上的判定依据。

    优先级：标签里的专辑类型 → 同目录音频数（==1 单曲 / >1 专辑曲目）→ 缺专辑 →
    专辑名等于曲名 → 默认按专辑曲目。``sibling_audio_count`` 由调用方一次遍历统计好，
    这里只做判断，不碰磁盘。

    「目录里多于一首」排在「缺专辑 / 专辑名等于曲名」前面：本库的单曲是独占一个目录，
    同住一个目录的必然是专辑曲目，按单曲处理就会把该补的专辑标签漏掉。
    """
    tag = (album_type or "").strip()
    if any(hint in tag.casefold() for hint in _SINGLE_TAG_HINTS):
        return TrackKind.SINGLE, f"专辑类型为 {tag}"

    if sibling_audio_count == 1:
        return TrackKind.SINGLE, "目录内仅此 1 个音频文件"

    if sibling_audio_count is not None and sibling_audio_count > 1:
        return TrackKind.ALBUM, f"目录内有 {sibling_audio_count} 个音频文件"

    album = (meta.album or "").strip()
    if not album:
        return TrackKind.SINGLE, "缺少专辑标签"

    if album.casefold() == (meta.title or "").strip().casefold():
        return TrackKind.SINGLE, "专辑名与曲名相同"

    return TrackKind.ALBUM, "默认按专辑曲目处理"
