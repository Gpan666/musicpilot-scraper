"""Aggregate local tags and online providers into one confident metadata result."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx

from musicpilot.adapters.metadata.base import (
    REASON_NO_LYRICS,
    REASON_NO_RESPONSE,
    REASON_UNSUPPORTED,
    LyricsResult,
    candidate_from_dict,
)
from musicpilot.adapters.metadata.kugou import KugouProvider
from musicpilot.adapters.metadata.kuwo import KuwoProvider
from musicpilot.adapters.metadata.musicbrainz import MusicBrainzProvider
from musicpilot.adapters.metadata.netease import NetEaseProvider
from musicpilot.adapters.metadata.qq import QQMusicProvider
from musicpilot.adapters.store import Store
from musicpilot.adapters.tags import fetch_cover
from musicpilot.config import RuntimeSettings, Settings
from musicpilot.domain.matcher import (
    choose_best,
    merge_metadata,
    normalize,
    rank_candidates,
)
from musicpilot.domain.models import AudioFile, MetadataCandidate, TrackMetadata


@dataclass(frozen=True, slots=True)
class Resolution:
    metadata: TrackMetadata
    confidence: float
    source: str
    reason: str
    needs_review: bool = False
    candidates: list[MetadataCandidate] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata.as_dict(),
            "confidence": round(self.confidence, 4),
            "source": self.source,
            "reason": self.reason,
            "needs_review": self.needs_review,
            "candidates": [item.as_dict() for item in self.candidates[:5]],
        }


# 任务明细的说明列对用户展示，来源名一律用中文，不出现英文品牌以外的词。
SOURCE_LABELS = {
    "musicbrainz": "MusicBrainz",
    "netease": "网易云",
    "qq": "QQ音乐",
    "kugou": "酷狗音乐",
    "kuwo": "酷我音乐",
    "local": "本地标签",
}

# 顺序影响并发查询的顺序（结果仍统一打分排序），国内音源优先，中文曲库命中率更高。
PROVIDER_CLASSES = (
    ("musicbrainz", MusicBrainzProvider),
    ("netease", NetEaseProvider),
    ("qq", QQMusicProvider),
    ("kugou", KugouProvider),
    ("kuwo", KuwoProvider),
)


class MetadataService:
    def __init__(self, settings: Settings, store: Store) -> None:
        self.settings = settings
        self.store = store
        self.client: httpx.AsyncClient | None = None
        self.providers: dict[str, Any] = {}

    async def startup(self) -> None:
        headers = {
            "User-Agent": self.settings.musicbrainz_user_agent,
            "Accept": "application/json",
        }
        kwargs: dict[str, Any] = {
            "timeout": self.settings.request_timeout,
            "follow_redirects": True,
            "headers": headers,
        }
        proxy = self.settings.http_proxy.strip()
        if proxy:
            try:
                self.client = httpx.AsyncClient(proxy=proxy, **kwargs)  # httpx >= 0.28
            except TypeError:  # httpx 0.27
                self.client = httpx.AsyncClient(proxies=proxy, **kwargs)  # type: ignore[call-arg]
        else:
            self.client = httpx.AsyncClient(**kwargs)
        self.providers = {name: factory(self.client) for name, factory in PROVIDER_CLASSES}

    async def shutdown(self) -> None:
        if self.client is not None:
            await self.client.aclose()
            self.client = None
        self.providers = {}

    async def resolve(self, audio: AudioFile, runtime: RuntimeSettings) -> Resolution:
        local = audio.local_metadata
        missing = local.missing_fields()
        wants_online = (
            any(source != "local" for source in runtime.metadata_sources)
            and (
                bool(missing)
                or (runtime.embed_cover and not audio.has_embedded_cover)
                or runtime.write_lyrics
            )
        )
        if not wants_online or not local.title.strip():
            return Resolution(
                metadata=local,
                confidence=1.0 if not missing else 0.0,
                source="local",
                reason="未查在线，只用本地标签",
                needs_review=bool(missing),
            )

        candidates = await self._online_candidates(local, runtime)
        ranked = rank_candidates(local, candidates)
        best = choose_best(local, candidates, threshold=runtime.confidence_threshold)
        if best is None:
            return Resolution(
                metadata=local,
                confidence=ranked[0].score if ranked else 0.0,
                source="local",
                reason=(
                    f"匹配度 {ranked[0].score:.2f} 低于阈值，保留原标签"
                    if ranked
                    else "在线无结果，保留原标签"
                ),
                needs_review=bool(missing),
                candidates=ranked[:5],
            )

        merged = merge_metadata(local, best.metadata)
        still_missing = merged.missing_fields()
        return Resolution(
            metadata=merged,
            confidence=best.score,
            source=best.metadata.source,
            reason=(
                f"取自{SOURCE_LABELS.get(best.metadata.source, best.metadata.source)}"
                f"（匹配度 {best.score:.2f}）"
            ),
            needs_review=best.score < 0.8 or bool(still_missing),
            candidates=ranked[:5],
        )

    async def _online_candidates(
        self, local: TrackMetadata, runtime: RuntimeSettings
    ) -> list[TrackMetadata]:
        names = [name for name in runtime.metadata_sources if name in self.providers]
        if not names:
            return []
        cache_key = "|".join(
            (
                normalize(local.title),
                normalize(local.artist),
                normalize(local.album),
            )
        )
        cached = self.store.cache_get(cache_key)
        if isinstance(cached, list):
            return [candidate_from_dict(item) for item in cached if isinstance(item, dict)]

        requests = [
            self.providers[name].search(
                title=local.title,
                artist=local.artist,
                album=local.album,
                limit=5,
            )
            for name in names
        ]
        results = await asyncio.gather(*requests, return_exceptions=True)
        candidates: list[TrackMetadata] = []
        seen: set[tuple[str, str]] = set()
        for result in results:
            if isinstance(result, BaseException):
                continue
            for candidate in result:
                key = (normalize(candidate.title), normalize(candidate.artist))
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(candidate)
        if candidates:
            self.store.cache_set(cache_key, [item.as_dict() for item in candidates])
        return candidates

    async def cover(self, url: str | None) -> tuple[bytes, str] | None:
        if not url or self.client is None:
            return None
        return await fetch_cover(url, client=self.client)

    async def fetch_lyrics(self, meta: TrackMetadata) -> LyricsResult:
        """按候选自带的 id 取词，并且把「为什么没取到」一并带回来。

        三种失败不许混为一谈：来源没有取词能力 / 请求没回来 / 这首歌确实没词。
        只读取，不写任何文件。
        """
        if meta.lyrics and meta.lyrics.strip():
            return LyricsResult(text=meta.lyrics)
        if meta.source in ("", "local", "manual"):
            return LyricsResult(reason=REASON_UNSUPPORTED)
        if not meta.external_id:
            return LyricsResult(reason="候选缺少来源 id，无法按 id 取词")
        provider = self.providers.get(meta.source)
        if provider is None or not getattr(provider, "supports_lyrics", False):
            return LyricsResult(reason=REASON_UNSUPPORTED)
        fetch = getattr(provider, "lyrics", None)
        if not callable(fetch):
            return LyricsResult(reason=REASON_UNSUPPORTED)
        try:
            try:
                result = await fetch(meta.external_id, album=meta.album)
            except TypeError:
                # 个别来源的 lyrics 只接 id
                result = await fetch(meta.external_id)
        except Exception:  # noqa: BLE001 - 歌词是加分项，拿不到不影响主流程
            return LyricsResult(reason=REASON_NO_RESPONSE)
        if isinstance(result, LyricsResult):
            if result.text:
                return result
            return LyricsResult(reason=result.reason or REASON_NO_LYRICS)
        # 兼容只回字符串的老实现
        text = str(result or "").strip()
        return LyricsResult(text=text) if text else LyricsResult(reason=REASON_NO_LYRICS)

    async def lyrics_for(self, meta: TrackMetadata) -> str | None:
        """取歌词正文：拿不到就返回 None（原因见 :meth:`fetch_lyrics`）。"""
        return (await self.fetch_lyrics(meta)).text

    def source_status(self) -> dict[str, dict[str, Any]]:
        """每个源的真实能力：能不能搜、能不能按 id 取词、有什么要提前说明的限制。"""
        status: dict[str, dict[str, Any]] = {}
        for name in SOURCE_LABELS:
            if name == "local":
                continue
            provider = self.providers.get(name)
            status[name] = {
                "search": provider is not None,
                "lyrics": bool(provider is not None and getattr(provider, "supports_lyrics", False)),
                "lyrics_note": str(getattr(provider, "lyrics_note", "") or ""),
                "search_note": str(getattr(provider, "search_note", "") or ""),
            }
        return status


async def cover_embedding(path: Any) -> tuple[bytes, str] | None:
    """Embedded artwork of a source file, used as a preview fallback."""
    from musicpilot.adapters.scanner import embedded_cover

    return await asyncio.to_thread(embedded_cover, path)
