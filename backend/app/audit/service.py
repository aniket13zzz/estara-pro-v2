"""Audit log helper. Call ``record`` for approvals, visit confirmations, config changes,
data exports, kill-switch changes and security events. Entries are append-only."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


async def record(
    db: AsyncSession,
    action: str,
    *,
    actor_user_id: uuid.UUID | None = None,
    actor_type: str = "user",
    entity_type: str | None = None,
    entity_id: str | uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Add an audit entry to the current transaction (the caller commits)."""
    entry = AuditLog(
        action=action,
        actor_user_id=actor_user_id,
        actor_type=actor_type,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        details=details or {},
        ip_address=ip_address,
    )
    db.add(entry)
    await db.flush()
    return entry
