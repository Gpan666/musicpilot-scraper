"""在线音源：候选能否带出专辑艺术家与轨号，以及歌词接口的兜底逻辑。"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from musicpilot.adapters.metadata.base import candidate_from_dict, clean_lyrics
from musicpilot.adapters.metadata.kugou import KugouProvider
from musicpilot.adapters.metadata.kuwo import KuwoProvider
from musicpilot.adapters.metadata.netease import NetEaseProvider
from musicpilot.adapters.metadata.qq import QQMusicProvider
from musicpilot.domain.matcher import merge_metadata
from musicpilot.domain.models import TrackMetadata


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_merge_keeps_online_album_artist() -> None:
    """专辑艺术家刮不出来的根因：在线给了 album_artist，却在合并时被丢掉。"""
    local = TrackMetadata(title="Bohemian Rhapsody", artist="Queen")
    remote = TrackMetadata(
        title="Bohemian Rhapsody",
        artist="Queen",
        album="A Night at the Opera",
        album_artist="Queen & Various",
    )
    merged = merge_metadata(local, remote)
    assert merged.album_artist == "Queen & Various"


def test_merge_falls_back_to_artist_when_online_has_nothing() -> None:
    local = TrackMetadata(title="Song")
    remote = TrackMetadata(title="Song", artist="Artist")
    assert merge_metadata(local, remote).album_artist == "Artist"


def test_missing_album_artist_counts_as_missing() -> None:
    meta = TrackMetadata(title="t", artist="a", album="b", year=2020, track_number=1)
    assert "album_artist" in meta.missing_fields()


def test_netease_carries_album_artist() -> None:
    async def scenario() -> list[TrackMetadata]:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "result": {
                        "songs": [
                            {
                                "id": 1,
                                "name": "波西米亚狂想曲",
                                "artists": [{"name": "Queen"}, {"name": "David Bowie"}],
                                "album": {
                                    "name": "歌剧院之夜",
                                    "artist": {"name": "Queen"},
                                    "publishTime": 0,
                                },
                            }
                        ]
                    }
                },
            )

        async with _client(handler) as client:
            return await NetEaseProvider(client).search(title="波西米亚狂想曲")

    candidates = asyncio.run(scenario())
    assert candidates[0].artist == "Queen, David Bowie"
    assert candidates[0].album_artist == "Queen"


def test_qq_search_parses_callback_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            'callback({"data": {"song": {"list": [{"mid": "abc", "title": "晴天", '
            '"singer": [{"name": "周杰伦"}], "index_album": 3, '
            '"album": {"name": "叶惠美", "mid": "alb", "release_date": "2003-07-31"}}]}}});'
        )
        return httpx.Response(200, text=body)

    async def scenario() -> list[TrackMetadata]:
        async with _client(handler) as client:
            return await QQMusicProvider(client).search(title="晴天")

    candidates = asyncio.run(scenario())
    assert candidates[0].title == "晴天"
    assert candidates[0].artist == "周杰伦"
    assert candidates[0].album == "叶惠美"
    assert candidates[0].track_number == 3
    assert candidates[0].external_id == "abc"


def test_qq_lyrics_unwraps_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='MusicJsonCallback({"lyric": "[00:01.00]第一行\\n[00:02.00]第二行"});',
        )

    async def scenario() -> str | None:
        async with _client(handler) as client:
            return await QQMusicProvider(client).lyrics("abc")

    assert scenario.__name__ == "scenario"
    assert asyncio.run(scenario()) == "[00:01.00]第一行\n[00:02.00]第二行"


def test_kugou_strips_krc_wrapper() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "krcs" in str(request.url):
            return httpx.Response(200, json={"candidates": [{"id": "1", "accesskey": "k"}]})
        return httpx.Response(
            200,
            json={"content": 'krc1|"[00:01.00]你好\\r\\n[00:02.00]世界"|'},
        )

    async def scenario() -> str | None:
        async with _client(handler) as client:
            return await KugouProvider(client).lyrics("hash")

    assert asyncio.run(scenario()) == "[00:01.00]你好\n[00:02.00]世界"


def test_kuwo_search_and_lyrics() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "lyric" in str(request.url).lower():
            return httpx.Response(200, json={"data": {"lyric": "[00:01.00]一句歌词"}})
        return httpx.Response(
            200,
            json={
                "data": {
                    "list": [
                        {
                            "rid": 228908,
                            "name": "夜曲",
                            "artist": "周杰伦",
                            "album": "十一月的萧邦",
                            "albumartist": "周杰伦",
                            "pic": "https://img1.kwcdn.kuwo.cn/x.jpg",
                        }
                    ]
                }
            },
        )

    async def scenario() -> tuple[list[TrackMetadata], str | None]:
        async with _client(handler) as client:
            provider = KuwoProvider(client)
            found = await provider.search(title="夜曲")
            lyrics = await provider.lyrics("228908")
            return found, lyrics

    candidates, lyrics = asyncio.run(scenario())
    assert candidates[0].album == "十一月的萧邦"
    assert candidates[0].album_artist == "周杰伦"
    assert candidates[0].external_id == "228908"
    assert lyrics == "[00:01.00]一句歌词"


def test_clean_lyrics_drops_blank_lines() -> None:
    assert clean_lyrics("[00:01.00]a\n\n\n[00:02.00]b\n") == "[00:01.00]a\n[00:02.00]b"
    assert clean_lyrics("   ") is None


def test_candidate_roundtrip_keeps_album_artist_and_track() -> None:
    meta = candidate_from_dict(
        {"title": "t", "artist": "a", "album_artist": "aa", "track_number": "7"}
    )
    assert meta.album_artist == "aa"
    assert meta.track_number == 7
