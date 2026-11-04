"""Aggregate local tags and online providers into one confident metadata result."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx

from musicpilot.adapters.metadata.base import MetadataProvider, candidate_from_dict
from musicpilot.adapters.metadata.musicbrainz import MusicBrainzProvider
from musicpilot.adapters.metadata.netease import NetEaseProvider
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


class MetadataService:
    def __init__(self, settings: Settings, store: Store) -> None:
        self.settings = settings
        self.store = store
        self.client: httpx.AsyncClient | None = None
        self.providers: dict[str, MetadataProvider] = {}

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
        self.providers = {
            "musicbrainz": MusicBrainzProvider(self.client),
            "netease": NetEaseProvider(self.client),
        }

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
                reason="仅使用本地标签与文件名（未启用在线查询或缺少可用标题）",
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
                    f"在线候选最高匹配度 {ranked[0].score:.2f} 低于阈值 "
                    f"{runtime.confidence_threshold:.2f}，未采用在线结果"
                    if ranked
                    else "在线元数据源没有返回结果，保留本地信息"
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
                f"采用 {best.metadata.source} 候选（匹配度 {best.score:.2f}），"
                f"仅补全本地缺失字段"
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

    async def lyrics_for(self, meta: TrackMetadata) -> str | None:
        if meta.lyrics or meta.source != "netease" or not meta.external_id:
            return meta.lyrics
        provider = self.providers.get("netease")
        if isinstance(provider, NetEaseProvider):
            return await provider.lyrics(meta.external_id)
        return None
