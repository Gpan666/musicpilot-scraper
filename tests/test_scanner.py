"""Scanner robustness against mutagen's inconsistent tag backends.

mutagen's Vorbis-family tags (FLAC/Ogg/Opus) raise ``ValueError`` on a *missing*
key instead of returning ``None`` like the MP3/ID3 backend. A scan must survive
that and keep going.
"""

from pathlib import Path

from musicpilot.adapters.scanner import _safe_get, read_tags


class _FakeVorbis:
    """Mimic mutagen's Vorbis backend: ``.get()`` raises ValueError for missing keys."""

    def get(self, key, default=None):  # noqa: ARG002 - mimic mutagen behaviour
        raise ValueError

    @property
    def info(self):
        class _Info:
            length = 180.0
            bitrate = 320000

        return _Info()

    def close(self):
        return None


def test_safe_get_catches_vorbis_valueerror() -> None:
    fake = _FakeVorbis()
    assert _safe_get(fake, "date") is None
    assert _safe_get(fake, "APIC") is None


def test_read_tags_survives_vorbis_valueerror(monkeypatch) -> None:
    monkeypatch.setattr(
        "musicpilot.adapters.scanner.MutagenFile", lambda *a, **k: _FakeVorbis()
    )
    metadata, has_cover, technical = read_tags(Path("/tmp/does-not-matter.ogg"))
    assert metadata is not None
    assert metadata.title == ""
    assert has_cover is False
    assert technical["duration"] == 180.0


# --- 内嵌封面判定的回归：ID3 的封面帧键名是 ``APIC:<描述>`` ---------------------


def _mp3_with_apic(tmp_path: Path, desc: str) -> Path:
    """写一个带 ``APIC:<desc>`` 内嵌封面的 mp3（mutagen 存成 ``APIC:`` 或 ``APIC:cover``）。"""
    from mutagen.id3 import APIC, ID3

    from tests.audiofiles import make_mp3

    path = make_mp3(tmp_path / "with-cover.mp3", {"title": "Covered"})
    tags = ID3(str(path))
    tags.add(
        APIC(encoding=3, mime="image/png", type=3, desc=desc, data=b"\x89PNG" + b"0" * 32)
    )
    tags.save(str(path))
    return path


def test_read_tags_detects_apic_frame_with_empty_description(
    tmp_path: Path, monkeypatch
) -> None:
    from musicpilot.adapters.scanner import MutagenFile

    path = _mp3_with_apic(tmp_path, "")
    # 前提：mutagen 把空描述的封面帧存成 ``APIC:``，精确取 "APIC" 取不到
    assert "APIC:" in list(MutagenFile(str(path)).tags.keys())

    _metadata, has_cover, _technical = read_tags(path)
    assert has_cover is True


def test_read_tags_detects_apic_frame_with_description(tmp_path: Path) -> None:
    from musicpilot.adapters.scanner import embedded_cover

    path = _mp3_with_apic(tmp_path, "cover")
    _metadata, has_cover, _technical = read_tags(path)
    assert has_cover is True
    # 判「有封面」与取封面口径一致
    cover = embedded_cover(path)
    assert cover is not None and cover[0].startswith(b"\x89PNG")


def test_read_tags_without_cover_stays_false(tmp_path: Path) -> None:
    from tests.audiofiles import make_mp3

    path = make_mp3(tmp_path / "no-cover.mp3", {"title": "Bare"})
    _metadata, has_cover, _technical = read_tags(path)
    assert has_cover is False
