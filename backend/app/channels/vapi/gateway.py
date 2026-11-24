"""Tool gateway for native voice mode (ADR 0014): ``POST /api/v1/webhooks/vapi/tools``.

Vapi's model calls ESTARA's tools here. Each request:

1. is checked with the shared secret (as every Vapi endpoint);
2. is tied to the caller by Vapi's call id and the caller ID, never by anything the model or the
   caller says; the tools have no customer, lead or visit ids to pass;
3. stores the caller's new turns (lead page timeline, language detection) and reads budget,
   area and BHK from them with the same fixed rules as the Sales Brain;
4. answers each tool call once: a repeated ``toolCallId`` gets the stored first answer, so a
   retry never books a second visit;
5. re-scores the lead with the fixed rules.

Vapi requires HTTP 200 with one ``result`` or ``error`` string per call (docs.vapi.ai/tools/
custom-tools, read 2026-11-13); any failure becomes an ``error`` that tells the model to offer
the sales team, so a caller is never left without an answer.

At the end of the call, ``check_numbers`` lists numbers the agent said that no tool result or
caller turn contained. The model's words cannot be checked before they are spoken in this mode,
so the dashboard shows these for staff to review.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.extraction import extract
from app.brain.scripts import Scripts
from app.brain.signals import detect_signals
from app.brain.tools import ToolContext, run_tool
from app.channels.vapi import service
from app.channels.vapi.auth import verify_vapi_request
from app.channels.vapi.native import VAPI_TOOL_NAMES
from app.conversations.service import record_inbound, record_outbound
from app.core.config import ClientConfig
from app.core.enums import Channel, RecordStatus
from app.core.settings import Settings
from app.customers import memory
from app.db.models import (
    AgentState,
    Call,
    Conversation,
    Customer,
    Lead,
    Message,
    Objection,
    Project,
    VapiToolCall,
)
from app.handoff.service import in_working_hours
from app.leads import requirements as reqs
from app.leads.scoring import advance, evaluate
from app.leads.state import ensure_open_lead
from app.providers.embeddings import lazy_embedder
from app.truth.staleness import Clock, SystemClock
from app.usage.summary import month_usage

log = logging.getLogger("estara.vapi.tools")
router = APIRouter(prefix="/api/v1/webhooks/vapi", tags=["vapi"])

USER_ROLES = {"user"}
AGENT_ROLES = {"bot", "assistant"}
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
TIME_RE = re.compile(r"\b(\d{1,2})[:.](\d{2})\s*(?:am|pm|a\.m\.|p\.m\.)?", re.IGNORECASE)


# ------------------------------------------------------------------------------ parsing


def tool_calls_of(message: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """``(toolCallId, name, arguments)`` for each call. Vapi's docs show the name and arguments
    under ``function``; flat ``name``/``arguments`` are read too. Arguments may be a JSON
    string."""
    out = []
    for item in message.get("toolCallList") or []:
        item = service.as_dict(item)
        fn = service.as_dict(item.get("function"))
        call_id = item.get("id")
        name = fn.get("name") or item.get("name")
        args: Any = fn.get("arguments", item.get("arguments", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except json.JSONDecodeError:
                args = {}
        if isinstance(call_id, str) and isinstance(name, str):
            out.append((call_id[:200], name, args if isinstance(args, dict) else {}))
    return out


def turns_of(message: dict[str, Any]) -> list[tuple[str, str]]:
    """``(role, text)`` of caller and agent turns so far, from ``artifact.messages``."""
    out = []
    for item in service.as_dict(message.get("artifact")).get("messages") or []:
        item = service.as_dict(item)
        role, text = item.get("role"), item.get("message") or item.get("content")
        if role in USER_ROLES | AGENT_ROLES and isinstance(text, str) and text.strip():
            out.append(("user" if role in USER_ROLES else "agent", text.strip()))
    return out


# ------------------------------------------------------------------------------ turns


async def _localities(db: AsyncSession) -> list[str]:
    rows = await db.execute(
        select(Project.locality, Project.city).where(Project.status == RecordStatus.APPROVED)
    )
    return sorted({n for pair in rows.tuples() for n in pair if n})


async def _exists(db: AsyncSession, provider_id: str) -> Message | None:
    found: Message | None = await db.scalar(
        select(Message).where(Message.provider_message_id == provider_id)
    )
    return found


async def sync_turns(db: AsyncSession, config: ClientConfig, *, vapi_call_id: str,
                     conversation: Conversation, customer: Customer, lead: Lead,
                     turns: list[tuple[str, str]], now: datetime) -> str:  # fmt: skip
    """Store turns not stored yet (each once, by position) and apply the fixed rules to new
    caller turns. Returns the language of the caller's latest turn."""
    language = conversation.language or config.agent.default_language
    localities: list[str] | None = None
    user_n = agent_n = 0
    for position, (role, text) in enumerate(turns):
        # Turns saved together get times a microsecond apart, so the timeline keeps their order.
        at = now - timedelta(microseconds=len(turns) - position)
        if role == "user":
            user_n += 1
            pid = service.message_id(vapi_call_id, user_n)
            stored = await _exists(db, pid)
            if stored is not None:
                language = stored.language or language
                continue
            recorded = await record_inbound(
                db, conversation=conversation, customer=customer, text=text, config=config,
                now=at, provider_message_id=pid,
            )  # fmt: skip
            language = recorded.language
            if localities is None:
                localities = await _localities(db)
            found = extract(text, localities=localities)
            if not found.requirements.is_empty():
                merged, _ = await reqs.save(db, lead, found.requirements, source="rules")
                memory.remember_requirements(customer, merged.model_dump())
                await advance(db, lead, merged, config)
            kind = detect_signals(text).objection
            if kind is not None:
                objection = Objection(
                    lead_id=lead.id,
                    conversation_id=conversation.id,
                    objection_type=kind,
                    text=text[:1000],
                    language=language,
                )
                db.add(objection)
                memory.remember_objection(customer, kind)
        else:
            agent_n += 1
            pid = f"vapi:{vapi_call_id}:agent:{agent_n}"
            if await _exists(db, pid) is None:
                message = await record_outbound(db, conversation=conversation, text=text,
                                                language=language, now=at)  # fmt: skip
                message.provider_message_id = pid
    await db.flush()
    return language if language in config.agent.languages else config.agent.default_language


# ------------------------------------------------------------------------------ numbers


def _numbers(text: str) -> set[str]:
    # A clock time ("2:00 PM", "11.30") counts as its hour and minutes, never as "00" or "30"
    # on their own; the slot tools speak times the same way.
    text = TIME_RE.sub(lambda m: m.group(1) + ("" if m.group(2) == "00" else " " + m.group(2)),
                       text)  # fmt: skip
    found = {m.group().replace(",", "").rstrip(".") for m in NUMBER_RE.finditer(text)}
    return {n for n in found if len(n.replace(".", "")) > 1}  # single digits: BHK, counts


async def check_numbers(db: AsyncSession, call: Call, turns: list[tuple[str, str]],
                        config: ClientConfig) -> list[str]:  # fmt: skip
    """Numbers the agent said that no tool result, caller turn or greeting contained."""
    results = (await db.execute(
        select(VapiToolCall.result).where(VapiToolCall.vapi_call_id == call.vapi_call_id)
    )).scalars().all()  # fmt: skip
    allowed: set[str] = set()
    for result in results:
        allowed |= _numbers(json.dumps(result, ensure_ascii=False))
    for role, text in turns:
        if role == "user":
            allowed |= _numbers(text)
    scripts = Scripts(config)
    for lang in config.agent.languages:
        for name in ("greeting.md", "recording_disclosure.md", "voice_transfer.md"):
            allowed |= _numbers(scripts.text(lang, name))
    said: set[str] = set()
    for role, text in turns:
        if role == "agent":
            said |= _numbers(text)
    return sorted(said - allowed)


# ------------------------------------------------------------------------------ endpoint


def _settings(request: Request) -> Settings:
    s: Settings = request.app.state.settings
    return s


def _config(request: Request) -> ClientConfig:
    cfg: ClientConfig = request.app.state.client_config
    return cfg


def _clock(request: Request) -> Clock:
    clock: Clock = getattr(request.app.state, "clock", None) or SystemClock()
    return clock


def _answer(results: list[tuple[str, dict[str, Any] | None, str | None]]) -> JSONResponse:
    out = []
    for call_id, result, error in results:
        if error is not None:
            out.append({"toolCallId": call_id, "error": error})
        else:
            out.append({"toolCallId": call_id, "result": json.dumps(result, ensure_ascii=False)})
    return JSONResponse({"results": out})


FAILED = (
    "The lookup failed. Say the team will confirm that detail, then keep helping with other "
    "options or a visit. Do not hand over the call because of this."
)


def _shape_transfer(config: ClientConfig, scripts: Scripts, result: dict[str, Any],
                    language: str, now: datetime,
                    phone_call: bool = True) -> dict[str, Any]:  # fmt: skip
    """Phone version of transfer_to_human: transfer the live call only when someone can answer.
    A website (browser) call has no phone line to transfer, so the caller is offered a call back
    instead (live call 2026-11-18: the transfer could not work and the caller got nothing)."""
    if result.get("status") != "handed_off":
        return result
    if not phone_call:
        return {**result, "transfer_now": False,
                "instruction": "This is a website call, so it cannot be transferred. Offer a "
                "call back: ask for their mobile number, read it back, and once they confirm, "
                "call request_callback."}  # fmt: skip
    transfer = in_working_hours(config, now) or config.escalation.after_hours == "transfer_to_human"
    result = {**result, "transfer_now": transfer}
    if transfer:
        result["say"] = scripts.text(language, "voice_transfer.md")
        result["instruction"] = "Say the 'say' line, then call transferCall."
    return result


@router.post("/tools")
async def tool_calls(request: Request) -> JSONResponse:
    settings, config, clock = _settings(request), _config(request), _clock(request)
    verify_vapi_request(request, settings)
    try:
        body = json.loads(await request.body() or b"{}")
    except json.JSONDecodeError:
        body = {}
    message = service.as_dict(service.as_dict(body).get("message")) or service.as_dict(body)
    calls = tool_calls_of(message)
    vapi_call_id = service.call_id_of(message)
    if vapi_call_id is None:
        log.warning("tool-calls without call.id; keys: %s", sorted(message))
        return _answer([(cid, None, FAILED) for cid, _, _ in calls])
    started = time.perf_counter()
    try:
        answers = await _run(request, config, settings, clock, message, vapi_call_id, calls)
        log.info("tool-calls %s answered in %d ms", ",".join(n for _, n, _ in calls),
                 (time.perf_counter() - started) * 1000)  # fmt: skip
        return _answer(answers)
    except Exception:
        log.exception("tool gateway failed")
        return _answer([(cid, None, FAILED) for cid, _, _ in calls])


API_PATH = "/api/v1/webhooks/vapi/api"
# Read-only tools that may still answer when Vapi sent no call id (nothing is saved).
LOOKUP_TOOLS = {"search_properties", "get_property_details", "get_commercial_info",
                "search_knowledge", "get_visit_slots"}  # fmt: skip
NOT_SAVED = (
    "This could not be saved right now. Say so honestly and offer to try again in a moment. "
    "Never say it is booked or done."
)


def _plain(value: Any) -> str:
    """A static value from Vapi; an unrendered template ("{{ call.id }}") counts as missing."""
    text = str(value or "").strip()
    return "" if "{{" in text else text


@router.post("/api/{name}")
async def api_request_tool(name: str, request: Request) -> JSONResponse:
    """One ESTARA tool as a Vapi "API Request" tool (the comparison assistant, ADR 0015).

    Vapi sends the model's arguments as the JSON body. The call is identified by
    ``call_id={{ call.id }}`` (and on phone calls ``caller={{ customer.number }}``) in the URL
    or as static body fields. Without a call id nothing is saved: a lookup still answers on a
    rolled-back record, and a write (visit, call back) says "not_saved", so nothing is ever tied
    to the wrong caller. The answer is the tool result itself as JSON.
    """
    settings, config, clock = _settings(request), _config(request), _clock(request)
    verify_vapi_request(request, settings)
    try:
        body = json.loads(await request.body() or b"{}")
    except json.JSONDecodeError:
        body = {}
    args = dict(service.as_dict(body))
    query = request.query_params
    vapi_call_id = _plain(args.pop("call_id", None) or query.get("call_id"))[:200]
    caller = _plain(args.pop("caller", None) or query.get("caller"))
    if caller.isdigit():  # a "+" in a query string arrives as a space, stripped above
        caller = "+" + caller
    persist = True
    if not vapi_call_id:
        log.warning("api tool %s without call_id", name)
        if name not in LOOKUP_TOOLS:
            return JSONResponse({"status": "not_saved", "instruction": NOT_SAVED})
        # A lookup still answers, on a throw-away record that is rolled back: the caller hears
        # real homes instead of an error, and nothing is tied to the wrong person.
        vapi_call_id, persist = f"unlinked:{uuid.uuid4()}", False
    message: dict[str, Any] = {"call": {"id": vapi_call_id}}
    if caller:
        message["call"]["customer"] = {"number": caller}
    started = time.perf_counter()
    try:
        calls = [(f"api:{uuid.uuid4()}", name, args)]
        [(_, result, error)] = await _run(
            request, config, settings, clock, message, vapi_call_id, calls, persist=persist
        )
    except Exception:
        log.exception("api tool %s failed", name)
        return JSONResponse({"status": "error", "instruction": FAILED})
    log.info("api tool %s answered in %d ms", name, (time.perf_counter() - started) * 1000)
    return JSONResponse(result if error is None else {"status": "error", "instruction": error})


async def _run(request: Request, config: ClientConfig, settings: Settings, clock: Clock,
               message: dict[str, Any], vapi_call_id: str,
               calls: list[tuple[str, str, dict[str, Any]]], *, persist: bool = True,
               ) -> list[tuple[str, dict[str, Any] | None, str | None]]:  # fmt: skip
    scripts = Scripts(config)
    async with request.app.state.sessionmaker() as db:
        now = clock.now()
        call, customer, conversation = await service.start_or_get_call(
            db, config, vapi_call_id=vapi_call_id, number=service.caller_number(message), now=now
        )
        lead = await ensure_open_lead(db, customer, Channel.VOICE)
        language = await sync_turns(db, config, vapi_call_id=vapi_call_id,
                                    conversation=conversation, customer=customer, lead=lead,
                                    turns=turns_of(message), now=now)  # fmt: skip
        call.language = language
        state = await db.get(AgentState, 1)
        usage = await month_usage(db, config, now)
        stopped = (state is not None and state.paused) or usage.call_limit_reached
        ctx = ToolContext(db=db, config=config, clock=clock, now=now, customer=customer,
                          conversation=conversation, lead=lead, language=language,
                          scripts=scripts,
                          embedder=lazy_embedder(request.app.state, config, settings))  # fmt: skip
        answers: list[tuple[str, dict[str, Any] | None, str | None]] = []
        for call_id, name, args in calls:
            earlier = await db.scalar(
                select(VapiToolCall).where(VapiToolCall.tool_call_id == call_id)
            )
            if earlier is not None:
                answers.append((call_id, earlier.result, None))
                continue
            if name not in VAPI_TOOL_NAMES:
                answers.append((call_id, None, f"Unknown tool {name!r}. {FAILED}"))
                continue
            if stopped and name != "transfer_to_human":
                result: dict[str, Any] = {
                    "status": "agent_paused",
                    "instruction": "Say this, then call transfer_to_human.",
                    "say": scripts.paused_handoff(language),
                }
            else:
                result = await run_tool(ctx, name, args)
                if name == "transfer_to_human":
                    result = _shape_transfer(
                        config,
                        scripts,
                        result,
                        language,
                        now,
                        service.caller_number(message) is not None,
                    )
            db.add(VapiToolCall(tool_call_id=call_id, vapi_call_id=vapi_call_id, name=name,
                                arguments=args, result=result))  # fmt: skip
            answers.append((call_id, result, None))
        req = await reqs.load(db, lead)
        if not req.is_empty():
            await evaluate(db, lead, req, config, clock, now=now, log_unchanged=False)
        if persist:
            await db.commit()
        else:
            await db.rollback()
    return answers


async def finish_native_call(db: AsyncSession, config: ClientConfig, call: Call,
                             message: dict[str, Any], now: datetime) -> None:  # fmt: skip
    """End of a native call: store the remaining turns and list unverified numbers."""
    turns = turns_of(message)
    if any(role == "user" for role, _ in turns):
        # Also when the model never called a tool: the caller still gets a lead and timeline.
        call, customer, conversation = await service.start_or_get_call(
            db, config, vapi_call_id=call.vapi_call_id,
            number=call.caller_phone or service.caller_number(message), now=now,
        )  # fmt: skip
        lead = await ensure_open_lead(db, customer, Channel.VOICE)
        await sync_turns(db, config, vapi_call_id=call.vapi_call_id, conversation=conversation,
                         customer=customer, lead=lead, turns=turns, now=now)  # fmt: skip
        req = await reqs.load(db, lead)
        if not req.is_empty():
            await evaluate(db, lead, req, config, SystemClock(), now=now, log_unchanged=False)
    unverified = await check_numbers(db, call, turns, config)
    call.unverified_numbers = unverified or None
    if unverified:
        log.warning("call %s: agent said numbers not found in tool results: %s",
                    call.id, ", ".join(unverified))  # fmt: skip
    await db.flush()
