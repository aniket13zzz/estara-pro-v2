"""Auth dependencies used by every protected route.

``current_user`` validates the session cookie and, for state-changing methods, the CSRF token.
``require_role`` builds on it. The route-guard test checks that every /api route outside
``PUBLIC_API_PATHS`` depends on ``current_user``.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import digests_equal, token_digest
from app.auth.service import load_session
from app.core.enums import Role
from app.core.settings import Settings
from app.db.deps import get_db
from app.db.models import User, UserSession

SESSION_COOKIE = "estara_session"
CSRF_COOKIE = "estara_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# /api routes reachable without a dashboard session. Webhooks authenticate with provider
# signatures instead (Stages 4 and 6). Keep this list short and explicit.
PUBLIC_API_PATHS: frozenset[str] = frozenset(
    {
        "/api/v1/health",
        "/api/v1/health/live",
        "/api/v1/auth/login",
    }
)
PUBLIC_API_PREFIXES: tuple[str, ...] = (
    "/api/v1/webhooks/",  # provider webhooks: signature-verified, never session-authenticated
)


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: UserSession


async def current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> AuthContext:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    loaded = await load_session(db, settings, token)
    if loaded is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired or invalid")
    session, user = loaded

    if request.method not in SAFE_METHODS:
        header = request.headers.get(CSRF_HEADER, "")
        expected = session.csrf_token_hash
        given = token_digest(header, settings.session_secret.get_secret_value())
        if not header or not digests_equal(given, expected):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing or invalid CSRF token")

    return AuthContext(user=user, session=session)


CurrentUser = Annotated[AuthContext, Depends(current_user)]


def require_role(*roles: Role) -> Callable[..., Coroutine[Any, Any, AuthContext]]:
    """Dependency factory: the signed-in user must have one of ``roles``."""
    allowed = frozenset(roles)

    async def checker(auth: CurrentUser) -> AuthContext:
        if auth.user.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role cannot do this")
        return auth

    checker.__name__ = f"require_role_{'_or_'.join(sorted(allowed))}"
    return checker


AdminUser = Annotated[AuthContext, Depends(require_role(Role.CLIENT_ADMIN))]
StaffUser = Annotated[AuthContext, Depends(require_role(Role.CLIENT_ADMIN, Role.SALES_STAFF))]


def is_public_api_path(path: str) -> bool:
    return path in PUBLIC_API_PATHS or path.startswith(PUBLIC_API_PREFIXES)
