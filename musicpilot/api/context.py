"""One explicit dependency container, handed to routers through FastAPI's Depends."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import Request

from musicpilot.adapters.metadata.service import MetadataService
from musicpilot.adapters.store import Store
from musicpilot.config import Settings
from musicpilot.services.jobs import JobManager
from musicpilot.services.scraper import ScraperService
from musicpilot.services.settings_service import SettingsService

SESSION_COOKIE = "mp_session"
SESSION_MAX_AGE = 7 * 24 * 3600


@dataclass(slots=True)
class AppContext:
    base: Settings
    store: Store
    settings: SettingsService
    metadata: MetadataService
    scraper: ScraperService
    jobs: JobManager
    secret: str
    auth_enabled: bool
    started_at: float


def get_context(request: Request) -> AppContext:
    return request.app.state.context  # type: ignore[no-any-return]


def resolve_secret(settings: Settings) -> str:
    if settings.session_secret.strip():
        return settings.session_secret.strip()
    path: Path = settings.secret_path
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    generated = secrets.token_urlsafe(48)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(generated, encoding="utf-8")
    except OSError:
        pass
    return generated


def session_token(username: str, secret: str) -> str:
    import hashlib
    import hmac

    issued = str(int(time.time()))
    signature = hmac.new(
        secret.encode(), f"{username}.{issued}".encode(), hashlib.sha256
    ).hexdigest()
    return f"{username}.{issued}.{signature}"


def session_username(token: str | None, secret: str) -> str | None:
    """Return the signed-in username from a valid session token, else ``None``."""
    import hashlib
    import hmac

    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    candidate, issued, signature = parts
    if not candidate:
        return None
    try:
        age = time.time() - int(issued)
    except ValueError:
        return None
    if age < 0 or age > SESSION_MAX_AGE:
        return None
    expected = hmac.new(
        secret.encode(), f"{candidate}.{issued}".encode(), hashlib.sha256
    ).hexdigest()
    return candidate if hmac.compare_digest(signature, expected) else None


def verify_session(token: str | None, secret: str, username: str) -> bool:
    return session_username(token, secret) == username
