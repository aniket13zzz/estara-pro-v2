"""Dashboard auth API: login, logout, current user, and the admin user list."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record
from app.auth.deps import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    AdminUser,
    CurrentUser,
    get_settings_dep,
)
from app.auth.ratelimit import RateLimiter
from app.auth.service import AccountLockedError, AuthError, authenticate, revoke_session
from app.core.enums import Role
from app.core.settings import Settings
from app.db.deps import get_db
from app.db.models import User

router = APIRouter(prefix="/api/v1", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    role: Role

    @classmethod
    def of(cls, user: User) -> UserOut:
        return cls(id=user.id, email=user.email, name=user.name, role=user.role)


class LoginResponse(BaseModel):
    user: UserOut
    csrf_token: str
    expires_at: datetime


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _set_auth_cookies(
    response: Response, settings: Settings, session_token: str, csrf_token: str
) -> None:
    max_age = settings.session_ttl_hours * 3600
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    # Readable by the dashboard so it can echo it in the X-CSRF-Token header.
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/auth/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> LoginResponse:
    limiter: RateLimiter = request.app.state.rate_limiter
    ip = _client_ip(request) or "unknown"
    allowed = await limiter.hit(
        f"login:ip:{ip}", settings.login_ip_max_attempts, settings.login_ip_window_seconds
    )
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many login attempts. Please wait a few minutes and try again.",
            headers={"Retry-After": str(settings.login_ip_window_seconds)},
        )
    try:
        issued = await authenticate(
            db,
            settings,
            email=body.email,
            password=body.password,
            ip_address=ip,
            user_agent=request.headers.get("user-agent"),
        )
    except AccountLockedError as exc:
        raise HTTPException(
            status.HTTP_423_LOCKED,
            "This account is temporarily locked after repeated failed logins. Try again later.",
            headers={"Retry-After": str(settings.login_lockout_minutes * 60)},
        ) from exc
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password") from exc

    _set_auth_cookies(response, settings, issued.session_token, issued.csrf_token)
    return LoginResponse(
        user=UserOut.of(issued.user), csrf_token=issued.csrf_token, expires_at=issued.expires_at
    )


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    auth: CurrentUser,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    await revoke_session(db, auth.session.id)
    await record(db, "auth.logout", actor_user_id=auth.user.id, ip_address=_client_ip(request))
    await db.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response


@router.get("/auth/me", response_model=UserOut)
async def me(auth: CurrentUser) -> UserOut:
    return UserOut.of(auth.user)


@router.get("/users", response_model=list[UserOut])
async def list_users(
    auth: AdminUser, db: Annotated[AsyncSession, Depends(get_db)]
) -> list[UserOut]:
    result = await db.execute(select(User).order_by(User.created_at))
    return [UserOut.of(u) for u in result.scalars()]
