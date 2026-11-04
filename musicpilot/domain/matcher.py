"""Filename parsing, text normalisation and candidate scoring.

Everything here is pure: no network, no filesystem, no framework. Deterministic
rules make it easy to explain why a given online match was accepted or rejected.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from musicpilot.domain.models import MetadataCandidate, TrackMetadata

_NOISE_WORDS = (
    "official",
    "official video",
    "official audio",
    "lyrics",
    "lyric video",
    "mv",
    "hd",
    "hq",
    "remastered",
    "remaster",
    "live",
    "instrumental",
    "karaoke",
    "feat",
    "ft",
    "featuring",
)
_BRACKETED = re.compile(r"[\[(（【][^\])）】]*[\])）】]")
_TRACK_PREFIX = re.compile(r"^\s*(?:(\d{1,2})\s*[-.]?\s*)?(.*)$")
_SEPARATORS = re.compile(r"\s+[-–—~]\s+")
_PUNCTUATION = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def normalize(text: str | None) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", str(text)).casefold()
    value = _BRACKETED.sub(" ", value)
    value = value.replace("&", " and ")
    value = _PUNCTUATION.sub(" ", value)
    tokens = [token for token in value.split() if token not in _NOISE_WORDS and len(token) > 0]
    return " ".join(tokens).strip()


def similarity(left: str | None, right: str | None) -> float:
    a, b = normalize(left), normalize(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    a_tokens, b_tokens = set(a.split()), set(b.split())
    overlap = len(a_tokens & b_tokens) / max(1, len(a_tokens | b_tokens))
    if a in b or b in a:
        ratio = max(ratio, 0.9)
    return round(min(1.0, 0.65 * ratio + 0.35 * overlap), 4)


def clean_hint(text: str) -> str:
    cleaned = _BRACKETED.sub(" ", text)
    cleaned = re.sub(
        r"\b(320k|flac|mp3|wav|lossless|hires|24bit|16bit)\b",
        " ",
        cleaned,
        flags=re.I,
    )
    return re.sub(r"\s+", " ", cleaned).strip(" -_.[]()")


_GENERIC_DIRS = frozenset(
    {
        "downloads",
        "download",
        "inbox",
        "music",
        "media",
        "temp",
        "tmp",
        "未完成",
        "未完成下载",
        "新建文件夹",
    }
)


def parse_filename(path: str | Path, *, root: str | Path | None = None) -> TrackMetadata:
    """Best-effort metadata from a filename and its two parent directories.

    ``root`` is the configured source directory: files sitting directly in it
    must not inherit its name (e.g. ``downloads``) as an album or artist.
    """
    source = Path(path)
    stem = clean_hint(source.stem)
    parents = source.parent.parts
    album_hint = clean_hint(parents[-1]) if parents else ""
    artist_hint = clean_hint(parents[-2]) if len(parents) >= 2 else ""
    if root is not None and source.parent.resolve() == Path(root).resolve():
        album_hint = ""
        artist_hint = ""
    if album_hint.casefold() in _GENERIC_DIRS:
        album_hint = ""
    if artist_hint.casefold() in _GENERIC_DIRS:
        artist_hint = ""

    prefix = _TRACK_PREFIX.match(stem)
    track_number: int | None = None
    remainder = stem
    if prefix and prefix.group(1):
        track_number = int(prefix.group(1))
        remainder = prefix.group(2).strip()

    artist: str | None = None
    title = remainder
    parts = [part.strip() for part in _SEPARATORS.split(remainder) if part.strip()]
    if len(parts) >= 2:
        artist, title = parts[0], " - ".join(parts[1:])
    elif artist_hint and not any(token in remainder.casefold() for token in (" - ", "_")):
        artist = artist_hint

    album = album_hint or None
    if album and artist_hint and album.casefold() == artist_hint.casefold():
        album = None
    if title.casefold() == (album or "").casefold():
        title = remainder

    year: int | None = None
    year_match = re.search(r"\b(19|20)\d{2}\b", " ".join(parents[-2:]))
    if year_match:
        year = int(year_match.group(0))

    return TrackMetadata(
        title=title or stem,
        artist=artist,
        album=album,
        album_artist=artist,
        year=year,
        track_number=track_number,
        source="local",
        external_id=str(source),
    )


def score_candidate(query: TrackMetadata, candidate: TrackMetadata) -> float:
    """Confidence in [0, 1] that ``candidate`` describes the same recording."""
    title_score = similarity(query.title, candidate.title)
    if title_score < 0.35:
        return round(title_score * 0.4, 4)
    artist_score = similarity(query.artist, candidate.artist)
    album_score = similarity(query.album, candidate.album)
    weights = {"title": 0.52, "artist": 0.30, "album": 0.13, "year": 0.05}
    score = weights["title"] * title_score
    if query.artist and candidate.artist:
        score += weights["artist"] * artist_score
    elif query.artist or candidate.artist:
        score += weights["artist"] * 0.35
    else:
        score += weights["artist"] * 0.6
    if query.album and candidate.album:
        score += weights["album"] * album_score
    else:
        score += weights["album"] * 0.6
    if query.year and candidate.year:
        score += weights["year"] * (1.0 if query.year == candidate.year else 0.0)
    else:
        score += weights["year"] * 0.6
    return round(min(1.0, score), 4)


def rank_candidates(
    query: TrackMetadata,
    candidates: list[TrackMetadata],
) -> list[MetadataCandidate]:
    ranked = [
        MetadataCandidate(metadata=candidate, score=score_candidate(query, candidate))
        for candidate in candidates
    ]
    return sorted(ranked, key=lambda item: item.score, reverse=True)


def choose_best(
    query: TrackMetadata,
    candidates: list[TrackMetadata],
    *,
    threshold: float = 0.62,
) -> MetadataCandidate | None:
    ranked = rank_candidates(query, candidates)
    if not ranked:
        return None
    best = ranked[0]
    return best if best.score >= threshold else None


def merge_metadata(local: TrackMetadata, remote: TrackMetadata) -> TrackMetadata:
    """Fill missing local fields from an online match; never overwrite what is known."""
    return TrackMetadata(
        title=local.title or remote.title,
        artist=local.artist or remote.artist,
        album=local.album or remote.album,
        album_artist=local.album_artist or remote.album_artist or local.artist,
        year=local.year or remote.year,
        track_number=local.track_number or remote.track_number,
        disc_number=local.disc_number or remote.disc_number,
        lyrics=local.lyrics or remote.lyrics,
        cover_url=local.cover_url or remote.cover_url,
        source=remote.source if remote.source != "local" else local.source,
        external_id=remote.external_id or local.external_id,
    )
