from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from musicpilot.domain.models import TrackMetadata


class MetadataProvider(Protocol):
    name: str

    async def search(
        self,
        *,
        title: str,
        artist: str | None = None,
        album: str | None = None,
        limit: int = 5,
    ) -> list[TrackMetadata]: ...


def join_names(values: object) -> str | None:
    """把各家音源的歌手数组拼成一行，跳过空名。"""
    if not isinstance(values, list):
        return None
    names = [str(item).strip() for item in values if str(item or "").strip()]
    return ", ".join(dict.fromkeys(names)) or None


def candidate_from_dict(payload: dict[str, object]) -> TrackMetadata:
    def text(key: str) -> str | None:
        value = payload.get(key)
        return str(value) if value not in (None, "") else None

    def number(key: str) -> int | None:
        value = payload.get(key)
        try:
            return int(value) if value is not None else None  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    return TrackMetadata(
        title=text("title") or "",
        artist=text("artist"),
        album=text("album"),
        album_artist=text("album_artist"),
        year=number("year"),
        track_number=number("track_number"),
        disc_number=number("disc_number"),
        lyrics=text("lyrics"),
        cover_url=text("cover_url"),
        source=text("source") or "unknown",
        external_id=text("external_id") or "",
    )


# 取词失败的三种真实原因：接口不支持、请求没回来、歌确实没词。
# 三者不许混为一谈，更不许拿「没有」笼统盖掉「拿不到」。
REASON_UNSUPPORTED = "该来源不支持按 id 取词"
REASON_NO_RESPONSE = "接口无返回"
REASON_NO_LYRICS = "该歌曲没有歌词"


@dataclass(frozen=True, slots=True)
class LyricsResult:
    """一次按 id 取词的结果：有正文就给正文，没有就带上具体原因。"""

    text: str | None = None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.text)

    def as_dict(self) -> dict[str, object]:
        return {"lyrics": self.text, "reason": self.reason}


class SupportsLyrics(Protocol):
    """Providers whose search result carries no lyrics but who can fetch them by id."""

    async def lyrics(self, song_id: str, *, album: str | None = None) -> LyricsResult: ...


def clean_lyrics(text: str | None) -> str | None:
    """Normalise a lyrics payload: drop empty/placeholder lines, keep the LRC timestamps."""
    if not text:
        return None
    lines = [line.rstrip() for line in str(text).replace("\r\n", "\n").split("\n")]
    kept = [line for line in lines if line.strip() and line.strip() != "//"]
    return "\n".join(kept).strip() or None
