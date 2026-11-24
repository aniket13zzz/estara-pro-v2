"""Auth: login, wrong password, lockout, rate limit, CSRF, sessions and role checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from app.auth.ratelimit import MemoryRateLimiter
from app.auth.service import create_user
from app.core.config import ClientConfig
from app.core.enums import Role
from app.db.models import AuditLog, User, UserSession
from app.main import create_app
from tests.conftest import BACKEND_DIR, TEST_PASSWORD, make_test_settings


def alembic_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(Config(str(BACKEND_DIR / "alembic.ini"))).get_current_head()
    assert head is not None
    return head


ADMIN = "admin@example.com"
STAFF = "staff@example.com"
ORIGIN = {"Origin": "https://dashboard.test"}


async def _make_users(db: AsyncSession) -> None:
    await create_user(db, email=ADMIN, name="Admin", password=TEST_PASSWORD, role=Role.CLIENT_ADMIN)
    await create_user(db, email=STAFF, name="Staff", password=TEST_PASSWORD, role=Role.SALES_STAFF)
    await db.commit()


async def _login(api: AsyncClient, email: str, password: str = TEST_PASSWORD) -> str:
    r = await api.post(
        "/api/v1/auth/login", json={"email": email, "password": password}, headers=ORIGIN
    )
    assert r.status_code == 200, r.text
    csrf: str = r.json()["csrf_token"]
    return csrf


async def _audit_actions(db: AsyncSession) -> list[str]:
    db.expire_all()
    rows = await db.execute(select(AuditLog.action).order_by(AuditLog.created_at))
    return list(rows.scalars())


# ------------------------------------------------------------------------------- login


async def test_passwords_are_argon2(db: AsyncSession) -> None:
    await _make_users(db)
    user = (await db.execute(select(User).where(User.email == ADMIN))).scalar_one()
    assert user.password_hash.startswith("$argon2id$")
    assert TEST_PASSWORD not in user.password_hash


async def test_short_password_rejected(db: AsyncSession) -> None:
    with pytest.raises(ValueError, match="at least 12"):
        await create_user(
            db, email="x@example.com", name="X", password="short", role=Role.SALES_STAFF
        )


async def test_login_sets_secure_cookies_and_me_works(api: AsyncClient, db: AsyncSession) -> None:
    await _make_users(db)
    # Email matching is case-insensitive.
    r = await api.post(
        "/api/v1/auth/login",
        json={"email": "ADMIN@Example.com", "password": TEST_PASSWORD},
        headers=ORIGIN,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["role"] == "client_admin"

    cookies = {c.split("=", 1)[0]: c for c in r.headers.get_list("set-cookie")}
    session_cookie = cookies[SESSION_COOKIE].lower()
    assert "httponly" in session_cookie and "secure" in session_cookie
    assert "samesite=lax" in session_cookie
    assert "httponly" not in cookies[CSRF_COOKIE].lower()  # dashboard JS must read it

    me = await api.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json()["email"] == ADMIN

    # Only a digest of the token is stored.
    raw_token = api.cookies.get(SESSION_COOKIE)
    stored = (await db.execute(select(UserSession.token_hash))).scalars().all()
    assert raw_token not in stored and len(stored) == 1
    assert "auth.login" in await _audit_actions(db)


async def test_wrong_password_and_unknown_user_look_the_same(
    api: AsyncClient, db: AsyncSession
) -> None:
    await _make_users(db)
    wrong = await api.post(
        "/api/v1/auth/login", json={"email": ADMIN, "password": "not the password"}, headers=ORIGIN
    )
    unknown = await api.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "whatever123"},
        headers=ORIGIN,
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Wrong email or password"}
    assert SESSION_COOKIE not in api.cookies
    actions = await _audit_actions(db)
    assert "auth.login_failed" in actions and "auth.login_failed_unknown_user" in actions


async def test_inactive_user_cannot_log_in(api: AsyncClient, db: AsyncSession) -> None:
    await _make_users(db)
    await db.execute(update(User).where(User.email == STAFF).values(is_active=False))
    await db.commit()
    r = await api.post(
        "/api/v1/auth/login", json={"email": STAFF, "password": TEST_PASSWORD}, headers=ORIGIN
    )
    assert r.status_code == 401


async def test_unauthenticated_requests_are_rejected(api: AsyncClient) -> None:
    assert (await api.get("/api/v1/auth/me")).status_code == 401
    assert (await api.get("/api/v1/users")).status_code == 401


# ---------------------------------------------------------------------- lockout and limits


async def test_lockout_after_repeated_failures(api: AsyncClient, db: AsyncSession) -> None:
    await _make_users(db)
    for _ in range(5):  # settings.login_lockout_threshold
        r = await api.post(
            "/api/v1/auth/login", json={"email": STAFF, "password": "wrong-pass"}, headers=ORIGIN
        )
        assert r.status_code == 401
    # Even the right password is refused while locked.
    r = await api.post(
        "/api/v1/auth/login", json={"email": STAFF, "password": TEST_PASSWORD}, headers=ORIGIN
    )
    assert r.status_code == 423
    assert "locked" in r.json()["detail"]
    assert "auth.login_blocked_locked" in await _audit_actions(db)

    # After the lock expires, the right password works and the counter resets.
    await db.execute(
        update(User)
        .where(User.email == STAFF)
        .values(locked_until=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db.commit()
    await _login(api, STAFF)
    db.expire_all()
    user = (await db.execute(select(User).where(User.email == STAFF))).scalar_one()
    assert user.failed_login_count == 0 and user.locked_until is None


async def test_login_rate_limit_per_ip(
    db: AsyncSession, migrated_db: str, client_config: ClientConfig
) -> None:
    await _make_users(db)
    settings = make_test_settings(migrated_db, login_ip_max_attempts=3)
    app = create_app(
        config=client_config, settings=settings, rate_limiter=MemoryRateLimiter(), use_redis=False
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://api.test"
        ) as api:
            codes = []
            for _ in range(4):
                r = await api.post(
                    "/api/v1/auth/login",
                    json={"email": ADMIN, "password": "wrong-pass"},
                    headers=ORIGIN,
                )
                codes.append(r.status_code)
    assert codes == [401, 401, 401, 429]


# ------------------------------------------------------------------------ CSRF and sessions


async def test_state_change_needs_csrf_token(api: AsyncClient, db: AsyncSession) -> None:
    await _make_users(db)
    csrf = await _login(api, ADMIN)

    r = await api.post("/api/v1/auth/logout", headers=ORIGIN)
    assert r.status_code == 403 and "CSRF" in r.json()["detail"]
    r = await api.post("/api/v1/auth/logout", headers={**ORIGIN, CSRF_HEADER: "forged"})
    assert r.status_code == 403

    r = await api.post("/api/v1/auth/logout", headers={**ORIGIN, CSRF_HEADER: csrf})
    assert r.status_code == 204
    assert (await api.get("/api/v1/auth/me")).status_code == 401
    assert "auth.logout" in await _audit_actions(db)


async def test_cross_origin_post_rejected(api: AsyncClient, db: AsyncSession) -> None:
    await _make_users(db)
    r = await api.post(
        "/api/v1/auth/login",
        json={"email": ADMIN, "password": TEST_PASSWORD},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403


async def test_expired_session_rejected(api: AsyncClient, db: AsyncSession) -> None:
    await _make_users(db)
    await _login(api, ADMIN)
    await db.execute(
        update(UserSession).values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    await db.commit()
    assert (await api.get("/api/v1/auth/me")).status_code == 401


async def test_each_login_creates_a_separate_session(api: AsyncClient, db: AsyncSession) -> None:
    await _make_users(db)
    await _login(api, ADMIN)
    await _login(api, ADMIN)
    count = (await db.execute(select(func.count()).select_from(UserSession))).scalar_one()
    assert count == 2


# ------------------------------------------------------------------------------- roles


async def test_admin_can_list_users(api: AsyncClient, db: AsyncSession) -> None:
    await _make_users(db)
    await _login(api, ADMIN)
    r = await api.get("/api/v1/users")
    assert r.status_code == 200
    assert {u["email"] for u in r.json()} == {ADMIN, STAFF}
    assert all("password" not in str(u) for u in r.json())


async def test_sales_staff_cannot_use_admin_routes(api: AsyncClient, db: AsyncSession) -> None:
    await _make_users(db)
    await _login(api, STAFF)
    assert (await api.get("/api/v1/auth/me")).status_code == 200
    r = await api.get("/api/v1/users")
    assert r.status_code == 403


async def test_health_is_public(api: AsyncClient) -> None:
    r = await api.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["migration"] == alembic_head()
    assert body["languages"] == ["en", "hi"]
    assert body["voice_ready"] is False
