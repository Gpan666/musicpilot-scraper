from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import make_audio, wait_for_job


def test_health_and_docs(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/").status_code == 200
    assert client.get("/app.js").status_code == 200
    assert client.get("/docs").status_code == 200
    about = client.get("/api/about").json()
    assert "version" in about


def test_auth_disabled_by_default(client: TestClient) -> None:
    status = client.get("/api/auth/status").json()
    assert status["enabled"] is False
    assert status["authenticated"] is True
    assert "未启用" in status["hint"]


def test_transfer_mode_documentation(client: TestClient) -> None:
    payload = client.get("/api/transfer-modes").json()
    assert [item["mode"] for item in payload["modes"]] == [
        "copy", "move", "hardlink", "symlink"
    ]
    assert "标签" in payload["ordering_note"]


def test_settings_roundtrip(client: TestClient) -> None:
    before = client.get("/api/settings").json()
    assert before["settings"]["transfer_mode"] == "copy"
    response = client.put(
        "/api/settings",
        json={"values": {"transfer_mode": "move", "naming_template": "{artist}/{title}"}},
    )
    assert response.status_code == 200
    after = response.json()["settings"]
    assert after["transfer_mode"] == "move"
    assert after["naming_template"] == "{artist}/{title}"
    invalid = client.put("/api/settings", json={"values": {"transfer_mode": "teleport"}})
    assert invalid.status_code == 422
    bad_template = client.put(
        "/api/settings", json={"values": {"naming_template": "{evil}"}}
    )
    assert bad_template.status_code == 422


def test_files_scan_and_detail(client: TestClient, workspace: dict[str, Any]) -> None:
    audio = make_audio(workspace["source"] / "01 - Queen - Bohemian Rhapsody.flac")
    listing = client.get("/api/files").json()
    assert listing["total"] == 1
    assert listing["files"][0]["display_name"] == audio.name
    detail = client.post("/api/files/detail", json={"path": str(audio)}).json()
    assert detail["filename_guess"]["artist"] == "Queen"
    assert detail["preview_target"].endswith(".flac")
    outside = workspace["root"] / "secret.flac"
    make_audio(outside)
    assert client.post("/api/files/detail", json={"path": str(outside)}).status_code == 400


def test_preview_then_execute_copy(client: TestClient, workspace: dict[str, Any]) -> None:
    audio = make_audio(workspace["source"] / "01 - Queen - Bohemian Rhapsody.flac")
    preview = client.post("/api/jobs", json={"kind": "preview", "paths": [str(audio)]})
    assert preview.status_code == 202
    job = wait_for_job(client, preview.json()["id"])
    assert job["status"] == "completed"
    items = client.get(f"/api/jobs/{job['id']}/items").json()["items"]
    assert len(items) == 1
    assert items[0]["status"] in {"planned", "needs_review"}
    assert audio.exists(), "dry-run must not touch the source file"
    assert not any(workspace["library"].iterdir()), "dry-run must not create library files"

    client.put("/api/settings", json={"values": {"transfer_mode": "copy"}})
    execution = client.post(
        "/api/jobs",
        json={"kind": "scrape", "dry_run": False, "paths": [str(audio)]},
    )
    assert execution.status_code == 202
    finished = wait_for_job(client, execution.json()["id"])
    assert finished["status"] == "completed"
    results = client.get(f"/api/jobs/{finished['id']}/items").json()["items"]
    assert results[0]["status"] == "done"
    assert results[0]["mode"] == "copy"
    assert audio.exists()
    produced = list(workspace["library"].rglob("*.flac"))
    assert len(produced) == 1
    assert produced[0].read_bytes() == audio.read_bytes()

    report = client.get(f"/api/jobs/{finished['id']}/report")
    assert report.status_code == 200
    assert "源文件" in report.content.decode("utf-8-sig")


def test_move_mode_removes_source(client: TestClient, workspace: dict[str, Any]) -> None:
    client.put("/api/settings", json={"values": {"transfer_mode": "move"}})
    audio = make_audio(workspace["source"] / "song.mp3")
    job = client.post(
        "/api/jobs", json={"kind": "scrape", "dry_run": False, "paths": [str(audio)]}
    ).json()
    finished = wait_for_job(client, job["id"])
    assert finished["status"] == "completed"
    assert not audio.exists()
    assert len(list(workspace["library"].rglob("*.mp3"))) == 1


def test_conflicting_jobs_are_rejected(client: TestClient, workspace: dict[str, Any]) -> None:
    for index in range(4):
        make_audio(workspace["source"] / f"track-{index}.flac")
    first = client.post("/api/jobs", json={"kind": "preview"})
    assert first.status_code == 202
    second = client.post("/api/jobs", json={"kind": "preview"})
    assert second.status_code in {202, 409}
    if second.status_code == 409:
        assert "正在运行" in second.json()["detail"]
    wait_for_job(client, first.json()["id"])
