"""Route guard, behavioural half: call every /api operation in the OpenAPI schema without a
session and expect 401 (public routes excepted). Independent of FastAPI router internals."""

from __future__ import annotations

import re

from httpx import AsyncClient

from app.auth.deps import is_public_api_path

PARAM = re.compile(r"\{[^}]+\}")
DUMMY_ID = "00000000-0000-0000-0000-000000000000"


async def test_every_operation_rejects_anonymous_calls(api: AsyncClient) -> None:
    schema = (await api.get("/api/openapi.json")).json()
    checked: list[str] = []
    leaks: list[str] = []
    for path, operations in schema["paths"].items():
        if not path.startswith("/api") or is_public_api_path(path):
            continue
        url = PARAM.sub(DUMMY_ID, path)
        for method in operations:
            r = await api.request(
                method.upper(), url, json={}, headers={"Origin": "https://dashboard.test"}
            )
            checked.append(f"{method.upper()} {path}")
            if r.status_code != 401:
                leaks.append(f"{method.upper()} {path} -> {r.status_code}")
    assert len(checked) >= 3, f"too few operations checked: {checked}"
    assert leaks == [], f"routes reachable without a session: {leaks}"


async def test_schema_lists_public_routes_too(api: AsyncClient) -> None:
    paths = (await api.get("/api/openapi.json")).json()["paths"]
    assert "/api/v1/health" in paths and "/api/v1/auth/login" in paths
