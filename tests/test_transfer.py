from __future__ import annotations

import os
from pathlib import Path

import pytest

from musicpilot.domain.transfer import (
    ConflictPolicy,
    TransferMode,
    describe_modes,
    inspect_modes,
    transfer,
)


def test_four_modes_are_documented() -> None:
    modes = describe_modes()
    assert [item["mode"] for item in modes] == ["copy", "move", "hardlink", "symlink"]
    for item in modes:
        assert item["label"] and item["how"] and item["pros"] and item["cons"]
        assert item["tag_effect"] and item["use_when"] and item["fails_when"]


@pytest.mark.parametrize("mode", [TransferMode.COPY, TransferMode.MOVE])
def test_copy_and_move(tmp_path: Path, mode: TransferMode) -> None:
    source = tmp_path / "in" / "song.flac"
    source.parent.mkdir()
    source.write_bytes(b"payload")
    target = tmp_path / "out" / "renamed.flac"
    result = transfer(source, target, mode=mode)
    assert result.status == "transferred"
    assert Path(result.target or "").read_bytes() == b"payload"
    if mode is TransferMode.COPY:
        assert source.exists()
    else:
        assert not source.exists()


def test_hardlink_shares_inode_and_survives_source_delete(tmp_path: Path) -> None:
    source = tmp_path / "song.flac"
    source.write_bytes(b"payload")
    target = tmp_path / "out" / "song.flac"
    result = transfer(source, target, mode=TransferMode.HARDLINK)
    if result.status == "failed" and "同一" in result.detail:
        pytest.skip("platform/filesystem does not support hard links here")
    assert result.status == "transferred"
    assert os.stat(source).st_ino == os.stat(target).st_ino
    source.unlink()
    assert target.read_bytes() == b"payload"


def test_symlink_uses_relative_target(tmp_path: Path) -> None:
    source = tmp_path / "in" / "song.flac"
    source.parent.mkdir()
    source.write_bytes(b"payload")
    target = tmp_path / "out" / "song.flac"
    result = transfer(source, target, mode=TransferMode.SYMLINK)
    if result.status == "failed":
        pytest.skip(f"symlink not permitted here: {result.detail}")
    assert result.status == "transferred"
    assert target.is_symlink()
    assert os.path.isabs(result.link_target or "") is False
    assert target.read_bytes() == b"payload"
    # A relative link keeps working even if the whole tree is relocated.
    assert (target.parent / (result.link_target or "")).resolve() == source.resolve()


def test_symlink_absolute_target(tmp_path: Path) -> None:
    source = tmp_path / "song.flac"
    source.write_bytes(b"payload")
    target = tmp_path / "out" / "song.flac"
    result = transfer(
        source, target, mode=TransferMode.SYMLINK, symlink_relative=False
    )
    if result.status == "failed":
        pytest.skip("symlink not permitted here")
    assert os.path.isabs(result.link_target or "")


@pytest.mark.parametrize(
    ("policy", "expect_status", "expect_name"),
    [
        (ConflictPolicy.SKIP, "skipped", None),
        (ConflictPolicy.RENAME, "transferred", "song (2).flac"),
        (ConflictPolicy.OVERWRITE, "transferred", "song.flac"),
    ],
)
def test_conflict_policies(
    tmp_path: Path,
    policy: ConflictPolicy,
    expect_status: str,
    expect_name: str | None,
) -> None:
    source = tmp_path / "song.flac"
    source.write_bytes(b"new")
    target = tmp_path / "out" / "song.flac"
    target.parent.mkdir()
    target.write_bytes(b"old")
    result = transfer(source, target, mode=TransferMode.COPY, conflict_policy=policy)
    assert result.status == expect_status
    if expect_name is None:
        assert target.read_bytes() == b"old"
    else:
        assert (tmp_path / "out" / expect_name).exists()


def test_source_missing_and_path_escape(tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    missing = tmp_path / "missing.flac"
    result = transfer(
        missing, library / "a.flac", mode=TransferMode.COPY, allowed_root=library
    )
    assert result.status == "failed"

    source = tmp_path / "song.flac"
    source.write_bytes(b"payload")
    escape = transfer(
        source,
        library / ".." / "outside.flac",
        mode=TransferMode.COPY,
        allowed_root=library,
    )
    assert escape.status == "failed"
    assert not (tmp_path / "outside.flac").exists()


def test_hardlink_refuses_different_devices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "song.flac"
    source.write_bytes(b"payload")
    target = tmp_path / "out" / "song.flac"
    import musicpilot.domain.transfer as module

    real_device_of = module.device_of
    calls = {"n": 0}

    def fake_device(path: Path) -> int:
        calls["n"] += 1
        return 1 if calls["n"] == 1 else 2

    monkeypatch.setattr(module, "device_of", fake_device)
    result = transfer(source, target, mode=TransferMode.HARDLINK)
    assert result.status == "failed"
    assert "同一" in result.detail
    assert source.exists()
    monkeypatch.setattr(module, "device_of", real_device_of)


def test_inspect_modes_reports_same_device(tmp_path: Path) -> None:
    source = tmp_path / "in"
    library = tmp_path / "out"
    source.mkdir()
    library.mkdir()
    report = {item.mode: item for item in inspect_modes(source, library)}
    assert report[TransferMode.HARDLINK].available is True
    assert report[TransferMode.HARDLINK].same_device is True
    probed = {item.mode: item for item in inspect_modes(source, library, probe=True)}
    assert all(item.probed for item in probed.values())
    assert probed[TransferMode.COPY].available is True
