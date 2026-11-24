"""Login, lockout and session lifecycle."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record
from app.auth.security import (
    hash_password,
    needs_rehash,
    new_token,
    token_digest,
    verify_password,
)
from app.core.enums import Role
from app.core.settings import Settings
from app.db.models import User, UserSession


class AuthError(Exception):
    """Wrong email or password. Deliberately does not say which."""


class AccountLockedError(Exception):
    def __init__(self, until: datetime) -> None:
        super().__init__("account temporarily locked")
        self.until = until


@dataclass(frozen=True)
class IssuedSession:
    session_token: str
    csrf_token: str
    expires_at: datetime
    user: User


def now() -> datetime:
    return datetime.now(UTC)


def normalise_email(email: str) -> str:
    return email.strip().lower()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(func.lower(User.email) == normalise_email(email)))
    return result.scalar_one_or_none()


async def create_user(
    db: AsyncSession, *, email: str, name: str, password: str, role: Role
) -> User:
    user = User(
        email=normalise_email(email), name=name, password_hash=hash_password(password), role=role
    )
    db.add(user)
    await db.flush()
    return user


async def authenticate(
    db: AsyncSession,
    settings: Settings,
    *,
    email: str,
    password: str,
    ip_address: str | None,
    user_agent: str | None,
) -> IssuedSession:
    """Verify credentials, apply lockout rules, and issue a new session. Commits."""
    user = await get_user_by_email(db, email)
    current = now()

    if user is not None and user.locked_until is not None and user.locked_until > current:
        await record(db, "auth.login_blocked_locked", actor_user_id=user.id, ip_address=ip_address)
        await db.commit()
        raise AccountLockedError(user.locked_until)

    ok = verify_password(user.password_hash if user else None, password)
    if user is None or not ok or not user.is_active:
        if user is not None:
            user.failed_login_count += 1
            details: dict[str, object] = {"failed_count": user.failed_login_count}
            if user.failed_login_count >= settings.login_lockout_threshold:
                user.locked_until = current + timedelta(minutes=settings.login_lockout_minutes)
                user.failed_login_count = 0
                details["locked_until"] = user.locked_until.isoformat()
            await record(
                db,
                "auth.login_failed",
                actor_user_id=user.id,
                details=details,
                ip_address=ip_address,
            )
        else:
            await record(
                db, "auth.login_failed_unknown_user", actor_type="anonymous", ip_address=ip_address
            )
        await db.commit()
        raise AuthError

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = current
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    secret = settings.session_secret.get_secret_value()
    session_token, csrf_token = new_token(), new_token()
    expires_at = current + timedelta(hours=settings.session_ttl_hours)
    db.add(
        UserSession(
            token_hash=token_digest(session_token, secret),
            csrf_token_hash=token_digest(csrf_token, secret),
            user_id=user.id,
            expires_at=expires_at,
            last_seen_at=current,
            ip_address=ip_address,
            user_agent=(user_agent or "")[:400] or None,
        )
    )
    await record(db, "auth.login", actor_user_id=user.id, ip_address=ip_address)
    await db.commit()
    return IssuedSession(session_token, csrf_token, expires_at, user)


async def load_session(
    db: AsyncSession, settings: Settings, session_token: str
) -> tuple[UserSession, User] | None:
    digest = token_digest(session_token, settings.session_secret.get_secret_value())
    result = await db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(
            UserSession.token_hash == digest,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now(),
            User.is_active.is_(True),
        )
    )
    row = result.first()
    return (row[0], row[1]) if row else None


async def revoke_session(db: AsyncSession, session_id: uuid.UUID) -> None:
    await db.execute(
        update(UserSession).where(UserSession.id == session_id).values(revoked_at=now())
    )
