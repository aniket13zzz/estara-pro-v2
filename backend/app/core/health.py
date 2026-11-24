"""Public health endpoints. They never expose secrets or customer data."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from app.channels.vapi.assistant import voice_ready

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health")
async def health(request: Request, response: Response) -> dict[str, Any]:
    checks: dict[str, str] = {}

    try:
        async with request.app.state.sessionmaker() as db:
            await db.execute(text("SELECT 1"))
            revision = (
                await db.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
        checks["database"] = "ok"
        checks["migration"] = revision or "none"
    except Exception:  # health must report, not raise
        checks["database"] = "error"

    redis = request.app.state.redis
    if redis is None:
        checks["redis"] = "not_configured"
    else:
        try:
            await redis.ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "error"

    cfg = request.app.state.client_config
    ok = checks.get("database") == "ok" and checks.get("redis") in {"ok", "not_configured"}
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if ok else "degraded",
        "checks": checks,
        "client": cfg.company.name,
        "languages": cfg.agent.languages,
        "voice_ready": voice_ready(cfg, request.app.state.settings),
        "template_version": cfg.template_version,
    }
