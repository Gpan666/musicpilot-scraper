"""Optional session auth plus a small in-memory login rate limiter.

Authentication is disabled when MP_ADMIN_PASSWORD is empty; the UI then shows a
clear warning that the service must only be exposed on a trusted network.
"""

from __future__ import annotations

import time
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from musicpilot.api.context import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    get_context,
    session_token,
    verify_session,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_FAILED_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
_MAX_FAILURES = 5
_WINDOW_SECONDS = 60.0


class LoginPayload(BaseModel):
    username: str
    password: str


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _rate_limited(ip: str) -> bool:
    now = time.time()
    attempts = [stamp for stamp in _FAILED_ATTEMPTS[ip] if now - stamp < _WINDOW_SECONDS]
    _FAILED_ATTEMPTS[ip] = attempts
    return len(attempts) >= _MAX_FAILURES


def require_session(request: Request) -> None:
    context = get_context(request)
    if not context.auth_enabled:
        return
    if not verify_session(
        request.cookies.get(SESSION_COOKIE), context.secret, context.base.admin_username
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要登录")


@router.get("/status")
def auth_status(request: Request) -> dict[str, object]:
    context = get_context(request)
    authenticated = not context.auth_enabled or verify_session(
        request.cookies.get(SESSION_COOKIE), context.secret, context.base.admin_username
    )
    return {
        "enabled": context.auth_enabled,
        "authenticated": authenticated,
        "username": context.base.admin_username,
        "hint": (
            "未设置 MP_ADMIN_PASSWORD，当前未启用登录认证；请仅在可信内网使用"
            if not context.auth_enabled
            else ""
        ),
    }


@router.post("/login")
def login(payload: LoginPayload, request: Request, response: Response) -> dict[str, object]:
    context = get_context(request)
    if not context.auth_enabled:
        return {"ok": True, "message": "认证未启用"}
    ip = _client_ip(request)
    if _rate_limited(ip):
        raise HTTPException(status_code=429, detail="登录失败次数过多，请稍后再试")
    if (
        payload.username != context.base.admin_username
        or payload.password != context.base.admin_password
    ):
        _FAILED_ATTEMPTS[ip].append(time.time())
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    _FAILED_ATTEMPTS.pop(ip, None)
    response.set_cookie(
        SESSION_COOKIE,
        session_token(context.base.admin_username, context.secret),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}


@router.post("/logout")
def logout(response: Response) -> dict[str, object]:
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}
