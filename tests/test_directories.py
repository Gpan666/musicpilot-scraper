from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


def test_directory_browser(client: TestClient, workspace: dict[str, Any]) -> None:
    (workspace["source"] / "Album A").mkdir()
    (workspace["source"] / ".hidden").mkdir()
    (workspace["source"] / "@eaDir").mkdir()
    (workspace["source"] / "song.flac").write_bytes(b"audio")

    root = client.get(
        "/api/files/directories", params={"path": str(workspace["root"])}
    )
    assert root.status_code == 200
    payload = root.json()
    names = [item["name"] for item in payload["entries"]]
    assert "downloads" in names and "music" in names
    assert "data" in names

    inside = client.get(
        "/api/files/directories", params={"path": str(workspace["source"])}
    ).json()
    children = [item["name"] for item in inside["entries"]]
    assert "Album A" in children
    assert ".hidden" not in children
    assert "@eaDir" not in children
    assert "song.flac" not in children  # files are not listed
    assert inside["parent"] == str(workspace["root"])


def test_directory_browser_blocks_escape(client: TestClient, workspace: dict[str, Any]) -> None:
    outside = workspace["root"].parent
    assert client.get(
        "/api/files/directories", params={"path": str(outside)}
    ).status_code == 403
    assert client.get(
        "/api/files/directories", params={"path": "../somewhere"}
    ).status_code == 400
    assert client.get(
        "/api/files/directories", params={"path": str(workspace["root"] / "missing")}
    ).status_code == 404
