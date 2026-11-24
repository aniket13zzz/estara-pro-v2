"""Vapi endpoints (public paths under /api/v1/webhooks/, protected by the shared secret).

``POST /api/v1/webhooks/vapi/llm/chat/completions`` is the assistant's "custom LLM". Vapi sends
an OpenAI-style chat completion request for each caller turn; we answer with the Sales Brain.
The request and response follow OpenAI's chat completions format (streamed as server-sent
events when ``stream`` is true). Vapi's docs do not list the extra fields it adds; we read the
call id from ``call.id`` and the caller number from ``call.customer.number`` when present.
If the call id is missing, the request's top-level keys are logged and the caller is handed to
a person, never left in silence (live check, docs/demo.md).

``POST /api/v1/webhooks/vapi/events`` receives server messages: ``assistant-request`` (phone
numbers without a fixed assistant), ``end-of-call-report`` and ``status-update``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.brain.scripts import Scripts
from app.brain.service import SalesBrain
from app.channels.vapi import service
from app.channels.vapi.assistant import MODEL_NAME, human_transfer_destination, voice_ready
from app.channels.vapi.auth import verify_vapi_request
from app.channels.vapi.gateway import finish_native_call
from app.conversations.language import reply_language
from app.core.config import ClientConfig
from app.core.settings import Settings
from app.db.models import AgentState
from app.handoff.service import in_working_hours
from app.providers.embeddings import lazy_embedder
from app.providers.llm import LLMClient, LLMNotConfigured, get_llm
from app.truth.staleness import Clock, SystemClock
from app.usage.summary import month_usage

log = logging.getLogger("estara.vapi")
router = APIRouter(prefix="/api/v1/webhooks/vapi", tags=["vapi"])

# If the brain has not answered after this long, speak a short filler line first.
FILLER_AFTER_SECONDS = 1.5


def _config(request: Request) -> ClientConfig:
    cfg: ClientConfig = request.app.state.client_config
    return cfg


def _settings(request: Request) -> Settings:
    s: Settings = request.app.state.settings
    return s


def _clock(request: Request) -> Clock:
    clock: Clock = getattr(request.app.state, "clock", None) or SystemClock()
    return clock


def _llm(request: Request) -> LLMClient | None:
    llm: LLMClient | None = getattr(request.app.state, "llm", None)
    if llm is not None:
        return llm
    try:
        return get_llm(_config(request), _settings(request))
    except LLMNotConfigured as exc:
        log.warning("voice call without an LLM: %s", exc)
        return None


async def _json(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Body must be JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "Body must be a JSON object")
    return body


# ------------------------------------------------------------------- OpenAI-format output


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # [{"type": "text", "text": "..."}]
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict)).strip()
    return ""


def user_turns(body: dict[str, Any]) -> list[str]:
    return [
        _content_text(m.get("content"))
        for m in body.get("messages") or []
        if isinstance(m, dict) and m.get("role") == "user"
    ]


def transfer_call(config: ClientConfig) -> dict[str, Any]:
    return {
        "index": 0,
        "id": f"call_{uuid.uuid4().hex[:12]}",
        "type": "function",
        "function": {
            "name": "transferCall",
            "arguments": json.dumps({"destination": config.escalation.human_transfer_number}),
        },
    }


def _chunk(cid: str, delta: dict[str, Any], finish: str | None = None) -> str:
    payload = {
        "id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
        "model": MODEL_NAME, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }  # fmt: skip
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def completion(text: str, transfer: dict[str, Any] | None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if transfer:
        message["tool_calls"] = [{k: v for k, v in transfer.items() if k != "index"}]
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:16]}", "object": "chat.completion",
        "created": int(time.time()), "model": MODEL_NAME,
        "choices": [{"index": 0, "message": message,
                     "finish_reason": "tool_calls" if transfer else "stop"}],
    }  # fmt: skip


# --------------------------------------------------------------------------- the turn


class TurnAnswer:
    def __init__(self, text: str, transfer: dict[str, Any] | None) -> None:
        self.text = text
        self.transfer = transfer


async def answer_turn(request: Request, body: dict[str, Any]) -> TurnAnswer:
    """Run one caller turn through the Sales Brain (own database session: the response may be
    streamed after the request's dependencies have closed)."""
    config, clock = _config(request), _clock(request)
    scripts = Scripts(config)
    vapi_call_id = service.call_id_of(body)
    if vapi_call_id is None:
        log.warning("custom-llm request without call.id; top-level keys: %s", sorted(body))
        return TurnAnswer(scripts.text(config.agent.default_language, "paused_handoff.md"),
                          transfer_call(config))  # fmt: skip
    turns = user_turns(body)
    if not turns or not turns[-1].strip():
        return TurnAnswer("", None)
    provider_id = service.message_id(vapi_call_id, len(turns))
    async with request.app.state.sessionmaker() as db:
        now = clock.now()
        call, _, conversation = await service.start_or_get_call(
            db, config, vapi_call_id=vapi_call_id, number=service.caller_number(body), now=now
        )
        earlier = await service.stored_reply(db, conversation, provider_id)
        if earlier is not None:
            await db.commit()
            return TurnAnswer(earlier, None)
        brain = SalesBrain(
            db,
            config,
            llm=_llm(request),
            clock=clock,
            embedder=lazy_embedder(request.app.state, config, _settings(request)),
        )
        reply = await brain.respond(
            conversation, turns[-1].strip(), provider_message_id=provider_id
        )
        call.language = reply.language
        await db.commit()
    transfer = None
    if reply.handed_off and (
        in_working_hours(config, now) or config.escalation.after_hours == "transfer_to_human"
    ):
        transfer = transfer_call(config)
    return TurnAnswer(reply.text, transfer)


def _filler(config: ClientConfig, language: str) -> str | None:
    if not config.voice.filler_lines:
        return None
    lines = Scripts(config).lines(language, "filler_lines.md")
    return random.choice(lines) if lines else None  # noqa: S311  (not security related)


async def safe_answer(request: Request, body: dict[str, Any]) -> TurnAnswer:
    """Never leave a caller in silence: any unexpected failure becomes a handoff."""
    config = _config(request)
    try:
        return await answer_turn(request, body)
    except Exception:
        log.exception("voice turn failed; handing the caller to a person")
        fallback = Scripts(config).text(config.agent.default_language, "paused_handoff.md")
        return TurnAnswer(fallback, transfer_call(config))


@router.post("/llm/chat/completions", response_model=None)
async def custom_llm(request: Request) -> StreamingResponse | JSONResponse:
    verify_vapi_request(request, _settings(request))
    body = await _json(request)
    config = _config(request)
    if not body.get("stream"):
        answer = await safe_answer(request, body)
        return JSONResponse(completion(answer.text, answer.transfer))

    async def events() -> AsyncIterator[str]:
        cid = f"chatcmpl-{uuid.uuid4().hex[:16]}"
        task = asyncio.create_task(safe_answer(request, body))
        yield _chunk(cid, {"role": "assistant", "content": ""})
        done, _ = await asyncio.wait({task}, timeout=FILLER_AFTER_SECONDS)
        if not done:
            turns = user_turns(body)
            language = reply_language(turns[-1]) if turns else config.agent.default_language
            if language not in config.agent.languages:
                language = config.agent.default_language
            filler = _filler(config, language)
            if filler:
                yield _chunk(cid, {"content": filler + " "})
        answer = await task
        if answer.text:
            yield _chunk(cid, {"content": answer.text})
        if answer.transfer:
            yield _chunk(cid, {"tool_calls": [answer.transfer]})
            yield _chunk(cid, {}, "tool_calls")
        else:
            yield _chunk(cid, {}, "stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})  # fmt: skip


# ------------------------------------------------------------------------- server events


@router.post("/events")
async def server_events(request: Request) -> JSONResponse:
    settings, config = _settings(request), _config(request)
    verify_vapi_request(request, settings)
    body = await _json(request)
    message = service.as_dict(body.get("message")) or body
    kind = message.get("type")
    if kind == "assistant-request":
        return JSONResponse(await assistant_request(request))
    if kind == "end-of-call-report":
        async with request.app.state.sessionmaker() as db:
            now = _clock(request).now()
            call = await service.record_end_of_call(db, config, message, now=now)
            if call is not None and config.voice.mode == "native":
                await finish_native_call(db, config, call, message, now)
            if call is not None and call.transcript and not call.summary_en:
                call.summary_en = await summarise_call(request, call.transcript)
            await db.commit()
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": True})  # status-update and others: nothing to do


async def assistant_request(request: Request) -> dict[str, Any]:
    """For a phone number set up to ask us at call start: our assistant, or a person."""
    config, settings = _config(request), _settings(request)
    async with request.app.state.sessionmaker() as db:
        state = await db.get(AgentState, 1)
        usage = await month_usage(db, config, _clock(request).now())
    paused = state is not None and state.paused
    over_limit = usage.call_limit_reached or usage.ai_limit_reached
    if paused or over_limit or not voice_ready(config, settings):
        reason = "paused" if paused else "usage limit" if over_limit else "voice not ready"
        log.info("assistant-request: forwarding to a person (%s)", reason)
        return human_transfer_destination(config)
    return {"assistantId": settings.vapi_assistant_id}


SUMMARY_INSTRUCTIONS = (
    "Summarise this phone call between a real-estate sales assistant and a caller for the "
    "company's sales team, in English, in at most four short sentences: what the caller wants "
    "(area, BHK, budget, timeline, loan), objections, and the outcome (visit requested, "
    "transferred, follow-up). Use only what the transcript says; do not add numbers or facts."
)


async def summarise_call(request: Request, transcript: str) -> str | None:
    from app.providers.llm import InstrumentedLLM, summarize

    llm = _llm(request)
    if llm is None:
        return None
    try:
        return await summarize(InstrumentedLLM(llm), text=transcript[:12000],
                               instructions=SUMMARY_INSTRUCTIONS)  # fmt: skip
    except Exception:  # a summary is a convenience; never fail the webhook
        log.warning("call summary failed", exc_info=True)
        return None
