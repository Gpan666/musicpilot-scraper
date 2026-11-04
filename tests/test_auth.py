from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from musicpilot.app import create_app


def test_registration_login_and_protection(workspace: dict[str, Any]) -> None:
    app = create_app()
    with TestClient(app) as client:
        status = client.get("/api/auth/status").json()
        assert status["needs_setup"] is True
        assert status["authenticated"] is False
        # All guarded endpoints are closed before the first account exists.
        assert client.get("/api/settings").status_code == 401

        weak = client.post(
            "/api/auth/register", json={"username": "admin", "password": "short"}
        )
        assert weak.status_code == 422
        invalid_name = client.post(
            "/api/auth/register", json={"username": "a b", "password": "long-enough"}
        )
        assert invalid_name.status_code == 422

        created = client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "strong-pass-123"},
        )
        assert created.status_code == 200
        assert created.json()["auto_login"] is True
        assert client.get("/api/settings").status_code == 200
        assert client.get("/api/auth/status").json()["is_admin"] is True

        # Registration is closed to anonymous callers once an account exists.
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/settings").status_code == 401
        second = client.post(
            "/api/auth/register",
            json={"username": "intruder", "password": "another-pass-123"},
        )
        assert second.status_code == 403

        bad = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong-password"},
        )
        assert bad.status_code == 401
        good = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "strong-pass-123"},
        )
        assert good.status_code == 200
        assert client.get("/api/settings").status_code == 200


def test_admin_can_create_additional_accounts(workspace: dict[str, Any]) -> None:
    app = create_app()
    with TestClient(app) as client:
        assert client.post(
            "/api/auth/register",
            json={"username": "admin", "password": "strong-pass-123"},
        ).status_code == 200
        extra = client.post(
            "/api/auth/register",
            json={"username": "family", "password": "family-pass-123"},
        )
        assert extra.status_code == 200
        assert extra.json()["auto_login"] is False
        # Current session is untouched.
        assert client.get("/api/auth/status").json()["username"] == "admin"
