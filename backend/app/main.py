"""FastAPI application factory.

Startup loads and validates ``client/client.config.yaml`` and the environment settings first.
If either is invalid the process exits with a readable list of problems; nothing is served.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from redis.asyncio import Redis

from app.auth.ratelimit import MemoryRateLimiter, RateLimiter, RedisRateLimiter
from app.auth.router import router as auth_router
from app.channels.vapi.assistant import voice_ready
from app.channels.vapi.gateway import router as vapi_tools_router
from app.channels.vapi.router import router as vapi_router
from app.channels.whatsapp.router import router as whatsapp_router
from app.core.config import ClientConfig, ConfigError, load_config
from app.core.diagnostics import router as diagnostics_router
from app.core.health import router as health_router
from app.core.logging import configure_logging
from app.core.settings import Settings
from app.datacenter.router import router as datacenter_router
from app.db.session import make_engine, make_sessionmaker
from app.jobs.inprocess import maintenance_loop
from app.sales.router import router as sales_router

log = logging.getLogger("estara")

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def create_app(
    *,
    config: ClientConfig | None = None,
    settings: Settings | None = None,
    rate_limiter: RateLimiter | None = None,
    use_redis: bool = True,
) -> FastAPI:
    config = config or load_config()
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        app.state.sessionmaker = make_sessionmaker(engine)
        with_redis = use_redis and settings.redis_enabled
        redis: Redis | None = Redis.from_url(settings.redis_url) if with_redis else None
        app.state.redis = redis
        if rate_limiter is not None:
            app.state.rate_limiter = rate_limiter
        elif redis is not None:
            app.state.rate_limiter = RedisRateLimiter(redis)
        else:
            app.state.rate_limiter = MemoryRateLimiter()
        log.info(
            "ESTARA started for %s (languages %s, voice ready: %s)",
            config.company.name,
            config.agent.languages,
            voice_ready(config, settings),
        )
        jobs: asyncio.Task[None] | None = None
        if settings.run_jobs_in_web:
            jobs = asyncio.create_task(maintenance_loop(app.state.sessionmaker, config, settings))
        try:
            yield
        finally:
            if jobs is not None:
                jobs.cancel()
            if redis is not None:
                await redis.aclose()
            await engine.dispose()

    app = FastAPI(
        title="ESTARA Pro API",
        version=config.template_version,
        lifespan=lifespan,
        docs_url=None if settings.environment == "production" else "/api/docs",
        openapi_url=None if settings.environment == "production" else "/api/openapi.json",
        redoc_url=None,
    )
    app.state.client_config = config
    app.state.settings = settings

    allowed_origins = {settings.dashboard_origin.rstrip("/")}

    @app.middleware("http")
    async def origin_guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Reject cross-site state-changing requests to the dashboard API (defence in depth
        alongside SameSite cookies and the CSRF token). Webhooks are exempt: they carry no
        cookies and are verified by provider signatures."""
        path = request.url.path
        if (
            request.method in UNSAFE_METHODS
            and path.startswith("/api/")
            and not path.startswith("/api/v1/webhooks/")
        ):
            origin = request.headers.get("origin")
            if origin is not None and origin.rstrip("/") not in allowed_origins:
                return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
        return await call_next(request)

    app.include_router(health_router)
    app.include_router(diagnostics_router)
    app.include_router(auth_router)
    app.include_router(datacenter_router)
    app.include_router(vapi_router)
    app.include_router(vapi_tools_router)
    app.include_router(whatsapp_router)
    app.include_router(sales_router)
    return app


def build_app_or_exit() -> FastAPI:
    configure_logging()
    try:
        settings = Settings()
    except ValidationError as exc:
        problems = "\n".join(
            f"  - {'.'.join(str(p) for p in e['loc']).upper()}: "
            f"{e['msg'].removeprefix('Value error, ')}"
            for e in exc.errors()
        )
        print(f"Invalid environment settings (.env):\n{problems}", file=sys.stderr)
        raise SystemExit(1) from None
    try:
        return create_app(settings=settings)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
