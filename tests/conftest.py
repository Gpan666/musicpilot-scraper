from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from musicpilot.app import create_app


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    source = tmp_path / "downloads"
    library = tmp_path / "music"
    data = tmp_path / "data"
    for directory in (source, library, data):
        directory.mkdir()
    monkeypatch.setenv("MP_SOURCE_DIR", str(source))
    monkeypatch.setenv("MP_LIBRARY_DIR", str(library))
    monkeypatch.setenv("MP_DATA_DIR", str(data))
    monkeypatch.setenv("MP_METADATA_SOURCES", "local")
    monkeypatch.setenv("MP_WRITE_TAGS", "false")
    monkeypatch.setenv("MP_EMBED_COVER", "false")
    monkeypatch.setenv("MP_WRITE_LYRICS", "false")
    monkeypatch.setenv("MP_ADMIN_PASSWORD", "")
    monkeypatch.setenv("MP_STATIC_DIR", str(Path(__file__).resolve().parent.parent / "web"))
    return {"root": tmp_path, "source": source, "library": library, "data": data}


@pytest.fixture
def client(workspace: dict[str, Any]) -> Any:
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def make_audio(path: Path, content: bytes = b"fake-audio-payload") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def wait_for_job(client: TestClient, job_id: str, timeout: float = 15.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get(f"/api/jobs/{job_id}").json()["job"]
        if payload["status"] not in {"pending", "running"}:
            return payload
        time.sleep(0.08)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s")
