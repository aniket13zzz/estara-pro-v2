"""Check every link a demo call depends on, and read Vapi's own record of the last call:

    docker compose run --rm --no-deps backend python -m app.channels.vapi.doctor

1. ESTARA has approved, fresh homes to talk about.
2. PUBLIC_BASE_URL is reachable from the internet (through the tunnel), and how fast.
3. A real tool request through the tunnel, the way Vapi sends it, gets homes back (a lookup
   without a call id: nothing is saved).
4. Assistant A and B in Vapi point at this PUBLIC_BASE_URL and carry the shared-secret credential.
5. Vapi's record of the most recent call: why it ended, and what each tool call got back.

Prints PASS / FAIL with the fix for each FAIL. Never prints a key or the secret.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sys
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.channels.vapi.assistant import VoiceNotReady, public_url
from app.channels.vapi.gateway import API_PATH
from app.channels.vapi.sync import _client
from app.core.config import load_config
from app.core.enums import RecordStatus
from app.core.settings import Settings
from app.db.models import Project
from app.db.session import make_engine, make_sessionmaker
from app.truth.service import TruthService, Unavailable
from app.truth.staleness import SystemClock

failures: list[str] = []


def ok(text: str) -> None:
    print(f"PASS  {text}")


def bad(text: str, fix: str) -> None:
    print(f"FAIL  {text}\n      Fix: {fix}")
    failures.append(text)


def note(text: str) -> None:
    print(f"      {text}")


async def check_data(settings: Settings) -> None:
    try:
        await _check_data(settings)
    except SQLAlchemyError as exc:
        bad(f"the database is not ready ({type(exc).__name__})",
            "docker compose up -d --force-recreate --wait")  # fmt: skip


async def _check_data(settings: Settings) -> None:
    config = load_config()
    engine = make_engine(settings.database_url)
    try:
        async with make_sessionmaker(engine)() as db:
            codes = list((await db.execute(select(Project.code).where(
                Project.status == RecordStatus.APPROVED))).scalars())  # fmt: skip
            if not codes:
                bad(
                    "ESTARA has no approved projects",
                    "docker compose run --rm --no-deps backend python -m app.seed",
                )
                return
            newest = await db.scalar(select(func.max(Project.approved_at)))
            truth = TruthService(db, config, SystemClock())
            prices = [await truth.price(code) for code in codes]
            usable = [p for p in prices if not isinstance(p, Unavailable)]
            age = (datetime.now(UTC) - newest).days if newest else None
            if not usable:
                reasons = {p.reason.value for p in prices if isinstance(p, Unavailable)}
                bad(f"{len(codes)} projects, but no price can be said ({', '.join(reasons)})",
                    "reload the data: docker compose run --rm --no-deps backend "
                    "python -m app.seed")  # fmt: skip
            else:
                ok(f"{len(codes)} projects, {len(usable)} with sayable prices "
                   f"(approved {age} days ago)")  # fmt: skip
    finally:
        await engine.dispose()


async def check_tunnel(settings: Settings, base: str,
                       transport: httpx.AsyncBaseTransport | None = None) -> None:  # fmt: skip
    secret = settings.vapi_webhook_secret.get_secret_value() if settings.vapi_webhook_secret else ""
    async with httpx.AsyncClient(timeout=15, transport=transport) as client:
        try:
            started = time.perf_counter()
            r = await client.get(base + "/api/v1/health/live")
            ms = (time.perf_counter() - started) * 1000
        except httpx.HTTPError as exc:
            bad(f"{base} is not reachable ({type(exc).__name__})",
                "local: start the tunnel, put its address in .env as PUBLIC_BASE_URL and run "
                "start-demo.ps1; cloud: check the Render service shows Live")  # fmt: skip
            return
        if r.status_code != 200:
            bad(f"{base} answers HTTP {r.status_code}, not ESTARA",
                "the tunnel points somewhere else: restart it with "
                "cloudflared tunnel --url http://localhost:8000")  # fmt: skip
            return
        ok(f"{base} reaches ESTARA in {ms:.0f} ms")
        started = time.perf_counter()
        r = await client.post(base + f"{API_PATH}/search_properties", json={"bhk": [3]},
                              headers={"Authorization": f"Bearer {secret}"})  # fmt: skip
        ms = (time.perf_counter() - started) * 1000
        if r.status_code == 401:
            bad("ESTARA refused the shared secret", "VAPI_WEBHOOK_SECRET in .env is empty or wrong")
            return
        found = r.json().get("matches") if r.status_code == 200 else None
        if found:
            ok(f"a tool request through the tunnel found {len(found)} homes in {ms:.0f} ms")
        else:
            bad(f"a tool request through the tunnel got HTTP {r.status_code}: {r.text[:200]}",
                "docker compose logs backend --tail 50 shows the error")  # fmt: skip


def _host(url: str) -> str:
    return urlparse(url).netloc


async def check_vapi(settings: Settings, base: str,
                     transport: httpx.AsyncBaseTransport | None = None) -> None:  # fmt: skip
    if settings.vapi_api_key is None:
        bad(
            "VAPI_API_KEY is empty",
            "put the Vapi private key in .env (cloud: Render > Environment)",
        )
        return
    want = _host(base)
    cred = settings.vapi_server_credential_id
    async with _client(settings, transport) as vapi:
        for label, assistant_id in (("A", settings.vapi_assistant_id),
                                    ("B", settings.vapi_assistant_id_b)):  # fmt: skip
            if not assistant_id:
                note(f"assistant {label}: no id in .env, skipped")
                continue
            r = await vapi.get(f"/assistant/{assistant_id}")
            if r.status_code != 200:
                bad(f"assistant {label} ({assistant_id}) not found in Vapi (HTTP {r.status_code})",
                    f"check VAPI_ASSISTANT_ID{'_B' if label == 'B' else ''} in .env")  # fmt: skip
                continue
            data = r.json()
            model = data.get("model") or {}
            urls: list[tuple[str, str | None]] = []
            for tool in model.get("tools") or []:
                server = tool.get("server") or {}
                if server.get("url"):
                    urls.append((server["url"], server.get("credentialId")))
            for tool_id in model.get("toolIds") or []:
                t = await vapi.get(f"/tool/{tool_id}")
                if t.status_code == 200 and t.json().get("url"):
                    urls.append((t.json()["url"], t.json().get("credentialId")))
            server_url = (data.get("server") or {}).get("url", "")
            name = data.get("name") or "?"
            if not urls:
                bad(f"assistant {label} ({name}) has no ESTARA tools",
                    "run the sync (A) or the api_tools command (B)")  # fmt: skip
                continue
            wrong = {u for u, _ in urls if _host(u) != want}
            nocred = [u for u, c in urls if cred and c != cred]
            if wrong:
                bad(f"assistant {label} ({name}): tools point at an old address "
                    f"({', '.join(sorted({_host(u) for u in wrong}))})",
                    "local: run start-demo.ps1; cloud: restart the Render service")  # fmt: skip
            elif nocred:
                bad(f"assistant {label} ({name}): {len(nocred)} tools lack the ESTARA credential",
                    "run the sync (A) / api_tools command (B) again")  # fmt: skip
            else:
                ok(f"assistant {label} ({name}): {len(urls)} tools point at {want}")
            if _host(server_url) != want:
                bad(f"assistant {label}: server (events) address is {_host(server_url) or 'empty'}",
                    "run the sync (A) / api_tools command (B) again")  # fmt: skip
        managed = {i for i in (settings.vapi_assistant_id, settings.vapi_assistant_id_b) if i}
        await last_call(vapi, managed)


FAILED_TOOL = ("refused", "failed", "error", "timed out", "timeout", "not reachable")
SLOW_MS = {"modelLatencyAverage": (1500, "the model is slow: pick a faster model in Vapi "
                                          "(Assistant > Model) and compare this number"),
           "voiceLatencyAverage": (700, "the voice is slow: pick a faster voice model in Vapi "
                                          "(for ElevenLabs a Flash or Turbo model, or Cartesia)"),
           "transcriberLatencyAverage": (700, "the transcriber is slow: try another one in Vapi "
                                               "(Assistant > Transcriber)")}  # fmt: skip


async def last_call(vapi: httpx.AsyncClient, managed: set[str] | None = None) -> None:
    r = await vapi.get("/call", params={"limit": 1})
    calls = r.json() if r.status_code == 200 else []
    if isinstance(calls, dict):
        calls = calls.get("results", [])
    if not calls:
        note("no calls found in Vapi yet")
        return
    call = calls[0]
    used = call.get("assistantId")
    if managed and used and used not in managed:
        bad(f"the last call used assistant {used}, which ESTARA does not set up",
            "use the ID from VAPI_ASSISTANT_ID for the website (cloud: Cloudflare variable "
            "ESTARA_ASSISTANT_ID; local: config.js), or delete that assistant in Vapi")  # fmt: skip
    print(f"\nLast call: {call.get('createdAt', '?')}, assistant {call.get('assistantId', '?')}, "
          f"ended: {call.get('endedReason', '?')}")  # fmt: skip
    messages = (call.get("artifact") or {}).get("messages") or call.get("messages") or []
    shown = 0
    tool_errors: list[str] = []
    for m in messages:
        role = str(m.get("role", ""))
        if "tool" not in role:
            continue
        if role == "tool_calls":
            for tc in m.get("toolCalls") or []:
                fn = tc.get("function") or {}
                print(f"  -> {fn.get('name')}({str(fn.get('arguments'))[:120]})")
        else:
            text = m.get("result") or m.get("message") or m.get("content") or json.dumps(m)
            print(f"  <- {m.get('name', '')}: {str(text)[:240]}")
            if (
                any(word in str(text).lower()[:300] for word in FAILED_TOOL)
                and "matches" not in str(text)[:300]
            ):
                tool_errors.append(str(text)[:160])
        shown += 1
    if not shown:
        note(
            "Vapi recorded no tool calls in that call: the model never asked ESTARA. Check the "
            "assistant's tools in the Vapi dashboard."
        )
    if tool_errors:
        bad(f"{len(tool_errors)} tool calls in the last call failed in Vapi: {tool_errors[0]}",
            "the assistant's tools point somewhere ESTARA is not: see the lines above")  # fmt: skip
    metrics = (call.get("artifact") or {}).get("performanceMetrics")
    if isinstance(metrics, dict):
        print("  timing (ms): " + ", ".join(f"{k}={v}" for k, v in metrics.items()
                                             if isinstance(v, (int, float)))[:400])  # fmt: skip
        for key, (limit, advice) in SLOW_MS.items():
            value = metrics.get(key)
            if isinstance(value, (int, float)) and value > limit:
                note(f"slow reply: {key}={value:.0f} ms, {advice}")


async def report(settings: Settings) -> tuple[bool, str]:
    """The same check as the command line, as text (for the admin-only diagnostics page)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            code = await main_async(settings)
        except httpx.HTTPError as exc:
            print(f"FAIL  could not reach Vapi ({type(exc).__name__})")
            code = 1
    return code == 0, buffer.getvalue()


async def main_async(settings: Settings | None = None) -> int:
    settings = settings or Settings()
    failures.clear()
    print("ESTARA demo check\n")
    await check_data(settings)
    try:
        base = public_url(settings)
    except VoiceNotReady as exc:
        bad(str(exc), "set PUBLIC_BASE_URL in .env to the tunnel address")
        return 1
    await check_tunnel(settings, base)
    await check_vapi(settings, base)
    print()
    if failures:
        print(f"{len(failures)} problem(s) found. Fix the first one, then run this again.")
        return 1
    print("Every link works. If calls still fail, send the 'Last call' lines above.")
    return 0


def main() -> int:
    try:
        return asyncio.run(main_async())
    except httpx.HTTPError as exc:
        print(f"FAIL  could not reach Vapi ({type(exc).__name__}); check the internet connection")
        return 1


if __name__ == "__main__":
    sys.exit(main())
