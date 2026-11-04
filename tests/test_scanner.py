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