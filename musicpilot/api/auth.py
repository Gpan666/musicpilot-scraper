"""Account-based authentication: first-run registration, login and sessions.

Passwords are stored as PBKDF2-HMAC-SHA256 with a per-user random salt using
only the Python standard library. The first account becomes the administrator;
later accounts can only be created by an authenticated administrator.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from musicpilot.adapters.store import Store
from musicpilot.api.context import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    get_context,
    session_token,
    session_username,
)
from musicpilot.config import Settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

_PBKDF2_ROUNDS = 200_000
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
_MIN_PASSWORD_LENGTH = 8
_FAILED_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
_MAX_FAILURES = 5
_WINDOW_SECONDS = 60.0
_DUMMY_HASH: str | None = None


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def bootstrap_admin(store: Store, settings: Settings) -> None:
    """Migrate a legacy ``MP_ADMIN_PASSWORD`` deployment into a real account."""
    if not settings.admin_password or store.user_count() > 0:
        return
    try:
        store.create_user(
            settings.admin_username, hash_password(settings.admin_password), is_admin=True
        )
    except sqlite3.IntegrityError:
        pass


def _dummy_hash() -> str:
    """A stable fake hash so unknown usernames cost the same as real ones."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("invalid-password")
    return _DUMMY_HASH


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _rate_limited(ip: str) -> bool:
    now = time.time()
    attempts = [stamp for stamp in _FAILED_ATTEMPTS[ip] if now - stamp < _WINDOW_SECONDS]
    _FAILED_ATTEMPTS[ip] = attempts
    return len(attempts) >= _MAX_FAILURES


def _current_user(request: Request) -> dict[str, Any] | None:
    context = get_context(request)
    username = session_username(request.cookies.get(SESSION_COOKIE), context.secret)
    if not username:
        return None
    return context.store.get_user(username)


def _set_session(response: Response, username: str, secret: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session_token(username, secret),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


def require_session(request: Request) -> None:
    if _current_user(request) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要登录")


@router.get("/status")
def auth_status(request: Request) -> dict[str, object]:
    user = _current_user(request)
    return {
        "enabled": True,
        "authenticated": user is not None,
        "username": user["username"] if user else None,
        "is_admin": bool(user["is_admin"]) if user else False,
        "needs_setup": get_context(request).store.user_count() == 0,
    }


@router.post("/register")
def register(
    payload: Credentials, request: Request, response: Response
) -> dict[str, object]:
    context = get_context(request)
    count = context.store.user_count()
    caller = _current_user(request)
    if count > 0 and not (caller and caller["is_admin"]):
        raise HTTPException(status_code=403, detail="已有账号，只有管理员可以新增用户")
    username = payload.username.strip()
    if not _USERNAME_RE.fullmatch(username):
        raise HTTPException(status_code=422, detail="用户名需为 3–32 位字母、数字、_ . -")
    if len(payload.password) < _MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=422, detail=f"密码至少 {_MIN_PASSWORD_LENGTH} 位"
        )
    try:
        context.store.create_user(
            username, hash_password(payload.password), is_admin=(count == 0)
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="用户名已存在") from exc
    first_account = count == 0
    if first_account:
        _set_session(response, username, context.secret)
    return {"ok": True, "username": username, "auto_login": first_account}


@router.post("/login")
def login(payload: Credentials, request: Request, response: Response) -> dict[str, object]:
    context = get_context(request)
    ip = _client_ip(request)
    if _rate_limited(ip):
        raise HTTPException(status_code=429, detail="登录失败次数过多，请稍后再试")
    user = context.store.get_user(payload.username.strip())
    # Always run one PBKDF2 to keep timing similar for unknown usernames.
    stored = user["password_hash"] if user else _dummy_hash()
    matched = verify_password(payload.password, stored)
    if not user or not matched:
        _FAILED_ATTEMPTS[ip].append(time.time())
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    _FAILED_ATTEMPTS.pop(ip, None)
    _set_session(response, user["username"], context.secret)
    return {"ok": True, "username": user["username"], "is_admin": bool(user["is_admin"])}


@router.post("/logout")
def logout(response: Response) -> dict[str, object]:
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}
