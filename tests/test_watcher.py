from __future__ import annotations

import time
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import make_audio, wait_for_job


def _enable_watch(client: TestClient, interval: float = 5.0) -> dict[str, Any]:
    response = client.put(
        "/api/watch", json={"enabled": True, "interval": interval, "settle": 0}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["enabled"] is True
    return payload


def _wait_for(predicate: Any, timeout: float = 15.0) -> Any:
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("condition not met in time")


def test_watch_defaults_off(client: TestClient) -> None:
    payload = client.get("/api/watch").json()
    assert payload["enabled"] is False
    assert payload["interval"] > 0
    assert payload["running"] is True


def test_watch_rejects_out_of_range_interval(client: TestClient) -> None:
    assert client.put("/api/watch", json={"interval": 1}).status_code == 422


def test_watch_scrapes_new_files(client: TestClient, workspace: dict[str, Any]) -> None:
    _enable_watch(client)
    make_audio(workspace["source"] / "01 - Queen - Bohemian Rhapsody.flac")

    def auto_jobs() -> list[dict[str, Any]]:
        jobs = client.get("/api/jobs?limit=20").json()["jobs"]
        return [job for job in jobs if job["kind"] == "auto" and job["status"] == "completed"]

    job = _wait_for(lambda: (auto_jobs() or [None])[0], timeout=20)
    assert job["label"] == "01 - Queen - Bohemian Rhapsody.flac"
    assert job["auto"] is True
    items = client.get(f"/api/jobs/{job['id']}/items").json()["items"]
    assert len(items) == 1
    assert workspace["library"].exists()


def test_watch_does_not_repeat_a_file(client: TestClient, workspace: dict[str, Any]) -> None:
    _enable_watch(client)
    _wait_for(lambda: client.get("/api/watch").json()["last_scan"], timeout=20)
    make_audio(workspace["source"] / "02 - Queen - Love of My Life.flac")

    def auto_count() -> int:
        jobs = client.get("/api/jobs?limit=20").json()["jobs"]
        return len([job for job in jobs if job["kind"] == "auto"])

    _wait_for(lambda: auto_count() >= 1, timeout=20)
    first = auto_count()
    time.sleep(3)
    assert auto_count() == first, "已经处理过的文件不应再次触发任务"


def test_watch_reset_forgets_seen_files(client: TestClient, workspace: dict[str, Any]) -> None:
    make_audio(workspace["source"] / "03 - Queen - Somebody to Love.flac")
    _enable_watch(client)
    # 首次扫描把现有文件当成老文件登记，不排任务、不计入 detected
    _wait_for(lambda: client.get("/api/watch").json()["tracked"] >= 1, timeout=20)
    reset = client.post("/api/watch/reset").json()
    assert reset["tracked"] == 0
    assert reset["detected"] == 0


def test_manual_preview_skips_auto_waits(client: TestClient, workspace: dict[str, Any]) -> None:
    """A manual job runs even with the watcher enabled; the two never race."""
    _enable_watch(client)
    audio = make_audio(workspace["source"] / "04 - Queen - We Will Rock You.flac")
    preview = client.post("/api/jobs", json={"kind": "preview", "paths": [str(audio)]})
    assert preview.status_code in {202, 409}
    if preview.status_code == 202:
        job = wait_for_job(client, preview.json()["id"])
        assert job["status"] == "completed"


def _auto_jobs(client: TestClient) -> list[dict[str, Any]]:
    jobs = client.get("/api/jobs?limit=50").json()["jobs"]
    return [job for job in jobs if job["kind"] == "auto"]


def test_watch_does_not_scrape_existing_files(client: TestClient, workspace: dict[str, Any]) -> None:
    """开启监控时源目录里的老文件只登记、不刮削。"""
    for index in range(3):
        make_audio(workspace["source"] / f"old-{index}.flac", b"old-payload")
    time.sleep(0.2)
    _enable_watch(client)

    _wait_for(lambda: client.get("/api/watch").json()["baselined"] >= 3, timeout=20)
    time.sleep(2)
    assert _auto_jobs(client) == []
    assert client.get("/api/watch").json()["tracked"] == 3


def test_seen_files_survive_a_restart(workspace: dict[str, Any]) -> None:
    """指纹落库：重启后既不会重刮老文件，也不会漏掉新文件。"""
    from fastapi.testclient import TestClient

    from musicpilot.app import create_app

    make_audio(workspace["source"] / "old-01.flac", b"old")
    time.sleep(0.2)

    with TestClient(create_app()) as first:
        first.post(
            "/api/auth/register",
            json={"username": "tester", "password": "test-password-123"},
        )
        _enable_watch(first)
        _wait_for(lambda: first.get("/api/watch").json()["tracked"] >= 1, timeout=20)
    assert _auto_jobs(first) == []

    with TestClient(create_app()) as second:
        second.post(
            "/api/auth/login",
            json={"username": "tester", "password": "test-password-123"},
        )
        payload = second.get("/api/watch").json()
        assert payload["enabled"] is True
        _wait_for(lambda: second.get("/api/watch").json()["tracked"] >= 1, timeout=20)
        time.sleep(2)
        assert _auto_jobs(second) == [], "重启后老文件不应被重新刮削"

        make_audio(workspace["source"] / "new-01.flac", b"new")
        _wait_for(lambda: _auto_jobs(second), timeout=20)
        assert len(_auto_jobs(second)) == 1


def test_watch_ignores_downloader_leftovers(client: TestClient, workspace: dict[str, Any]) -> None:
    """半成品文件名（.part / .!qB …）先不碰，避免抢着刮还在下载的文件。"""
    _enable_watch(client)
    make_audio(workspace["source"] / "05 - Queen - Innuendo.flac.part")
    time.sleep(3)
    assert _auto_jobs(client) == []
    assert client.get("/api/watch").json()["detected"] == 0
