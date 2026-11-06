"""在线音源：候选能否带出专辑艺术家与轨号，以及歌词接口的兜底逻辑。"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import httpx

from musicpilot.adapters.metadata.base import (
    REASON_NO_LYRICS,
    REASON_NO_RESPONSE,
    REASON_UNSUPPORTED,
    candidate_from_dict,
    clean_lyrics,
)
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


def test_qq_search_reads_musicu_item_song() -> None:
    """新搜索接口是标准 JSON：单曲在 req_1.data.body.item_song，mid 当取词 id。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "musicu.fcg" in str(request.url)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "req_1": {
                    "code": 0,
                    "data": {
                        "body": {
                            "item_song": [
                                {
                                    "mid": "abc",
                                    "name": "晴天",
                                    "title": "晴天",
                                    "singer": [{"name": "周杰伦"}],
                                    "index_album": 3,
                                    "album": {"name": "叶惠美", "mid": "alb"},
                                }
                            ]
                        }
                    },
                },
            },
        )

    async def scenario() -> list[TrackMetadata]:
        async with _client(handler) as client:
            return await QQMusicProvider(client).search(title="晴天")

    candidates = asyncio.run(scenario())
    assert candidates[0].title == "晴天"
    assert candidates[0].artist == "周杰伦"
    assert candidates[0].album == "叶惠美"
    assert candidates[0].track_number == 3
    assert candidates[0].external_id == "abc"


def test_qq_search_survives_an_empty_body() -> None:
    """接口没回来只当「没搜到」，不能让整次搜索抛异常。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 500001})

    async def scenario() -> list[TrackMetadata]:
        async with _client(handler) as client:
            return await QQMusicProvider(client).search(title="晴天")

    assert asyncio.run(scenario()) == []


def test_qq_lyrics_unwraps_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='MusicJsonCallback({"lyric": "[00:01.00]第一行\\n[00:02.00]第二行"});',
        )

    async def scenario() -> Any:
        async with _client(handler) as client:
            return await QQMusicProvider(client).lyrics("abc")

    result = asyncio.run(scenario())
    assert result.text == "[00:01.00]第一行\n[00:02.00]第二行"
    assert result.reason == ""


def test_kugou_search_reads_song_search_v2() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "song_search_v2" in str(request.url)
        return httpx.Response(
            200,
            json={
                "status": 1,
                "data": {
                    "lists": [
                        {
                            "FileHash": "HASH",
                            "SongName": "<em>晴天</em>",
                            "SingerName": "周杰伦",
                            "AlbumName": "叶惠美",
                        }
                    ]
                },
            },
        )

    async def scenario() -> list[TrackMetadata]:
        async with _client(handler) as client:
            return await KugouProvider(client).search(title="晴天")

    candidates = asyncio.run(scenario())
    assert candidates[0].title == "晴天"
    assert candidates[0].artist == "周杰伦"
    assert candidates[0].album == "叶惠美"
    assert candidates[0].external_id == "HASH"


def test_kugou_lyrics_decodes_base64_lrc() -> None:
    """fmt=lrc 回的是 base64 包裹的 LRC，解出来才是正文。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/search" in str(request.url):
            return httpx.Response(200, json={"candidates": [{"id": "1", "accesskey": "k"}]})
        assert "/download" in str(request.url)
        return httpx.Response(
            200, json={"content": base64.b64encode("[00:01.00]你好".encode()).decode()}
        )

    async def scenario() -> Any:
        async with _client(handler) as client:
            return await KugouProvider(client).lyrics("hash")

    assert asyncio.run(scenario()).text == "[00:01.00]你好"


def test_kugou_lyrics_strips_legacy_krc_wrapper() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/search" in str(request.url):
            return httpx.Response(200, json={"candidates": [{"id": "1", "accesskey": "k"}]})
        return httpx.Response(
            200,
            json={"content": base64.b64encode('krc1|"[00:01.00]你好\\r\\n[00:02.00]世界"|'.encode()).decode()},
        )

    async def scenario() -> Any:
        async with _client(handler) as client:
            return await KugouProvider(client).lyrics("hash")

    assert asyncio.run(scenario()).text == "[00:01.00]你好\n[00:02.00]世界"


def test_kugou_lyrics_reports_when_no_candidate() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"candidates": []})

    async def scenario() -> Any:
        async with _client(handler) as client:
            return await KugouProvider(client).lyrics("hash")

    result = asyncio.run(scenario())
    assert result.text is None
    assert result.reason == REASON_NO_LYRICS


def test_kuwo_search_and_lyrics() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "songinfoandlrc" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "lrclist": [
                            {"time": "1.0", "lineLyric": "一句歌词"},
                            {"time": "62.5", "lineLyric": "另一句"},
                        ]
                    }
                },
            )
        return httpx.Response(
            200,
            text=(
                "{'TOTAL':'1','abslist':[{'DC_TARGETID':'228908','SONGNAME':'夜曲',"
                "'ARTIST':'周杰伦','FARTIST':'周杰伦','ALBUM':'十一月的萧邦',"
                "'web_albumpic_short':'120/x.jpg','web_timingonline':'2005-11-01'}]}"
            ),
        )

    async def scenario() -> tuple[list[TrackMetadata], Any]:
        async with _client(handler) as client:
            provider = KuwoProvider(client)
            found = await provider.search(title="夜曲")
            lyrics = await provider.lyrics("228908")
            return found, lyrics

    candidates, lyrics = asyncio.run(scenario())
    assert candidates[0].album == "十一月的萧邦"
    assert candidates[0].artist == "周杰伦"
    assert candidates[0].external_id == "228908"
    assert candidates[0].year == 2005
    # lrclist 按秒回时间，拼回标准 LRC
    assert lyrics.text == "[00:01.00]一句歌词\n[01:02.50]另一句"


def test_kuwo_lyrics_reports_failed_lookup() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": None, "msg": "音乐查询失败", "status": 301}
        )

    async def scenario() -> Any:
        async with _client(handler) as client:
            return await KuwoProvider(client).lyrics("1")

    result = asyncio.run(scenario())
    assert result.text is None
    # 接口原话要带出来，不能笼统说「没有」
    assert "音乐查询失败" in result.reason
    assert result.reason != REASON_NO_LYRICS


def test_clean_lyrics_drops_blank_lines() -> None:
    assert clean_lyrics("[00:01.00]a\n\n\n[00:02.00]b\n") == "[00:01.00]a\n[00:02.00]b"
    assert clean_lyrics("   ") is None


def test_candidate_roundtrip_keeps_album_artist_and_track() -> None:
    meta = candidate_from_dict(
        {"title": "t", "artist": "a", "album_artist": "aa", "track_number": "7"}
    )
    assert meta.album_artist == "aa"
    assert meta.track_number == 7


def test_musicbrainz_declares_it_has_no_lyrics() -> None:
    """MusicBrainz 不存歌词：如实声明，别让前端以为点一下就能取到词。"""
    from musicpilot.adapters.metadata.musicbrainz import MusicBrainzProvider

    assert MusicBrainzProvider.supports_lyrics is False
    assert "歌词" in MusicBrainzProvider.lyrics_note
    assert not hasattr(MusicBrainzProvider, "lyrics")


def test_netease_lyrics_reports_specific_reasons() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"lrc": {"lyric": ""}, "tlyric": {}})

    async def scenario() -> Any:
        async with _client(handler) as client:
            return await NetEaseProvider(client).lyrics("1")

    assert asyncio.run(scenario()).reason == REASON_NO_LYRICS


def test_fetch_lyrics_separates_unsupported_from_no_lyrics() -> None:
    """三种失败必须分开：不支持 / 接口无返回 / 确实没词。"""
    import httpx as _httpx

    from musicpilot.adapters.metadata.service import MetadataService
    from musicpilot.adapters.store import Store
    from musicpilot.config import Settings

    settings = Settings()
    store = Store(settings.data_dir / "test.db")
    service = MetadataService(settings, store)

    class _NoLyrics:
        supports_lyrics = False

    class _Broken:
        supports_lyrics = True

        async def lyrics(self, song_id: str, *, album: str | None = None) -> Any:
            raise _httpx.ConnectError("boom")

    class _Empty:
        supports_lyrics = True

        async def lyrics(self, song_id: str, *, album: str | None = None) -> Any:
            from musicpilot.adapters.metadata.base import LyricsResult

            return LyricsResult(reason=REASON_NO_LYRICS)

    service.providers = {"musicbrainz": _NoLyrics(), "qq": _Broken(), "kugou": _Empty()}

    async def scenario() -> tuple[str, str, str]:
        unsupported = await service.fetch_lyrics(
            TrackMetadata(title="t", source="musicbrainz", external_id="1")
        )
        broken = await service.fetch_lyrics(TrackMetadata(title="t", source="qq", external_id="1"))
        empty = await service.fetch_lyrics(TrackMetadata(title="t", source="kugou", external_id="1"))
        return unsupported.reason, broken.reason, empty.reason

    reasons = asyncio.run(scenario())
    assert reasons == (REASON_UNSUPPORTED, REASON_NO_RESPONSE, REASON_NO_LYRICS)
