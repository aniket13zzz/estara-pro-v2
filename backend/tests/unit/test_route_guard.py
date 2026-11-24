"""Route guard, static half: every /api route except health, auth/login and webhooks must depend
on ``current_user`` (directly or through ``require_role``).

The behavioural half (call every route without a session, expect 401) lives in
tests/integration/test_route_guard_http.py. Both exist because FastAPI's router internals change
between versions; if one check's route discovery breaks, its sanity count fails loudly.

If this fails, a route was added without ``CurrentUser``/``require_role``. Add the dependency;
only extend PUBLIC_API_PATHS for routes that are genuinely public.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from starlette.routing import BaseRoute

from app.auth.deps import current_user, is_public_api_path
from app.core.config import ClientConfig
from app.main import create_app
from tests.conftest import make_test_settings


def iter_api_routes(routes: Iterable[BaseRoute]) -> Iterator[APIRoute]:
    """Yield APIRoutes, descending into included routers (eager or lazy, depending on version)."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        nested = getattr(route, "original_router", None) or getattr(route, "routes", None)
        if nested is not None:
            yield from iter_api_routes(getattr(nested, "routes", nested))


def _depends_on(dependant: Dependant, target: object) -> bool:
    return any(dep.call is target or _depends_on(dep, target) for dep in dependant.dependencies)


def unprotected_routes(app: FastAPI) -> list[str]:
    problems = []
    for route in iter_api_routes(app.routes):
        if not route.path.startswith("/api") or is_public_api_path(route.path):
            continue
        if not _depends_on(route.dependant, current_user):
            problems.append(f"{','.join(sorted(route.methods))} {route.path}")
    return problems


def make_guard_app(client_config: ClientConfig) -> FastAPI:
    settings = make_test_settings("postgresql+psycopg://unused@localhost/unused")
    return create_app(config=client_config, settings=settings, use_redis=False)


def test_every_api_route_requires_auth(client_config: ClientConfig) -> None:
    app = make_guard_app(client_config)
    found = [r.path for r in iter_api_routes(app.routes) if r.path.startswith("/api")]
    assert len(found) >= 5, f"route discovery found too few routes ({found}); guard is broken"
    assert unprotected_routes(app) == []


def test_guard_catches_an_unprotected_route(client_config: ClientConfig) -> None:
    app = make_guard_app(client_config)

    @app.get("/api/v1/leaky")
    async def leaky() -> dict[str, str]:
        return {"oops": "no auth"}

    assert unprotected_routes(app) == ["GET /api/v1/leaky"]


def test_public_list_is_small_and_explicit() -> None:
    assert is_public_api_path("/api/v1/health")
    assert is_public_api_path("/api/v1/auth/login")
    assert is_public_api_path("/api/v1/webhooks/vapi")
    assert not is_public_api_path("/api/v1/auth/me")
    assert not is_public_api_path("/api/v1/auth/logout")
    assert not is_public_api_path("/api/v1/users")
