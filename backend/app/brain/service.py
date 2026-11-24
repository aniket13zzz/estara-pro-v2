"""The Sales Brain: one channel-agnostic entry point, ``SalesBrain.respond(conversation, text)``.

Per customer message:

1. Record the message; the backend detects its language (ADR 0004).
2. Stop here with the client's paused-handoff script if the kill switch is on or the monthly
   LLM budget is used up; a handoff is opened so a person replies.
3. Read requirements with deterministic rules (budgets, BHK, timeline, loan, purpose, known
   localities), save them and apply ADR 0007 system transitions. Detect objections,
   escalation and "are you a bot?".
4. Build the prompt (one template, language block, backend notes) and run the model with the
   tools until it answers. Tool calls change data only through backend rules.
5. Guards: every number or date in the reply must come from a tool result, the customer or
   approved script text, and the reply must be in the conversation language. A failing reply
   is sent back once for a rewrite; if it still fails, ungrounded sentences are removed (and
   if nothing is left, a person takes over).
6. Enforce escalation: a request for a person or a legal question always opens a handoff,
   even if the model did not call ``transfer_to_human``. AI disclosure is added to the first
   text reply and whenever the customer asks.
7. Record the reply with its tool calls, guard results and LLM usage. The caller commits.

If the LLM fails (after retries), the customer gets the paused-handoff script and a handoff
with reason ``ai_failure``; nothing the model half-wrote is shown.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.extraction import extract
from app.brain.grounding import Claim, Grounding, language_ok
from app.brain.prompt import TurnNotes, build_system_prompt
from app.brain.scripts import Scripts
from app.brain.signals import ALWAYS_HAND_OFF, Signals, detect_signals
from app.brain.tools import ToolContext, run_tool, tool_specs
from app.conversations.service import history, record_inbound, record_outbound
from app.core.config import ClientConfig
from app.core.enums import Channel, LeadState, MessageDirection, RecordStatus, UsageKind
from app.customers import memory
from app.db.models import (
    AgentState,
    Conversation,
    Customer,
    Lead,
    Message,
    Objection,
    Project,
    SiteVisit,
    UsageRecord,
)
from app.handoff.service import create_handoff
from app.leads import requirements as reqs
from app.leads.scoring import advance, evaluate
from app.leads.state import Actor, ensure_open_lead, transition
from app.providers.embeddings import Embedder
from app.providers.llm import (
    ChatResult,
    InstrumentedLLM,
    LLMClient,
    LLMError,
    LLMRateLimited,
    LLMTimeout,
    LLMUnavailable,
    Pacer,
    RetryPolicy,
    ToolResult,
    Turn,
)
from app.truth.staleness import Clock, SystemClock
from app.usage.recorder import llm_usage_recorder
from app.visits.slots import HOLDING_STATES

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6
# Room for the provider's thinking tokens (Gemini and OpenAI count them as output); the prompt
# keeps the visible reply short.
REPLY_MAX_TOKENS = 4096
HISTORY_LIMIT = 24
# Tools whose results the reply may quote numbers from. Document snippets are excluded.
UNGROUNDING_TOOLS = frozenset({"search_knowledge"})
HAND_OFF_FROM = (
    LeadState.NEW_ENQUIRY, LeadState.ENGAGED, LeadState.QUALIFIED, LeadState.VISIT_REQUESTED,
    LeadState.VISIT_CONFIRMED, LeadState.NURTURING,
)  # fmt: skip


@dataclass
class BrainReply:
    text: str
    language: str
    message: Message
    handed_off: bool
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Turn:
    """Everything one ``respond`` call works with."""

    conversation: Conversation
    customer: Customer
    lead: Lead
    language: str
    inbound: Message
    signals: Signals
    scripts: Scripts
    now: datetime
    tool_log: list[dict[str, Any]] = field(default_factory=list)
    grounding: Grounding = field(default_factory=Grounding)
    meta: dict[str, Any] = field(default_factory=dict)


class _NoReply(Exception):
    """The model ended without any text."""


class SalesBrain:
    def __init__(
        self,
        db: AsyncSession,
        config: ClientConfig,
        *,
        llm: LLMClient | None,
        clock: Clock | None = None,
        embedder: Callable[[], Embedder] | None = None,
        retry: RetryPolicy | None = None,
        pacer: Pacer | None = None,
        on_llm_wait: Callable[[str], None] | None = None,
    ) -> None:
        self.db = db
        self.config = config
        self.llm = llm
        self.clock = clock or SystemClock()
        self.embedder = embedder
        self.retry = retry or RetryPolicy()
        self.pacer = pacer  # share one across conversations to space calls (free tiers)
        self.on_llm_wait = on_llm_wait

    # --------------------------------------------------------------------------- entry

    async def respond(
        self, conversation: Conversation, text: str, *, provider_message_id: str | None = None
    ) -> BrainReply:
        db, config = self.db, self.config
        now = self.clock.now()
        customer = await db.get(Customer, conversation.customer_id)
        if customer is None:
            raise ValueError("conversation has no customer")
        is_first_reply = not await self._has_outbound(conversation)
        recorded = await record_inbound(
            db, conversation=conversation, customer=customer, text=text, config=config, now=now,
            provider_message_id=provider_message_id,
        )  # fmt: skip
        lead = await ensure_open_lead(db, customer, conversation.channel)
        signals = detect_signals(text)
        turn = _Turn(conversation, customer, lead, recorded.language, recorded.message, signals,
                     Scripts(config), now)  # fmt: skip
        turn.meta["language"] = {"reply": recorded.language, "switched": recorded.switched}
        inbound_meta: dict[str, Any] = {"signals": signals.as_dict()}

        stop = await self._stopped(now)
        if stop is not None:
            recorded.message.meta = inbound_meta
            return await self._fallback(turn, reason=stop)

        inbound_meta["extracted"] = await self._apply_rules(turn, text)
        recorded.message.meta = inbound_meta
        await self._record_objection(turn, text)

        if self.llm is None:
            return await self._fallback(turn, reason="ai_failure", detail="no LLM configured")
        llm = InstrumentedLLM(
            self.llm, policy=self.retry, pacer=self.pacer, on_wait=self.on_llm_wait,
            recorder=llm_usage_recorder(db, config, conversation_id=conversation.id),
        )  # fmt: skip
        try:
            reply = await self._converse(turn, llm)
        except LLMRateLimited as exc:
            # For the customer this is an AI failure (a person takes over). It is marked so
            # reports and the eval can tell provider limits from Sales Brain failures.
            log.warning("LLM rate limited for conversation %s: %s", conversation.id, exc)
            turn.meta["llm_error"] = "rate_limited_daily" if exc.daily else "rate_limited"
            return await self._fallback(turn, reason="ai_failure", detail=str(exc)[:200],
                                        llm=llm)  # fmt: skip
        except LLMTimeout as exc:
            log.warning("LLM timed out for conversation %s: %s", conversation.id, exc)
            turn.meta["llm_error"] = "timeout"
            return await self._fallback(turn, reason="ai_failure", detail=str(exc)[:200],
                                        llm=llm)  # fmt: skip
        except LLMUnavailable as exc:
            # Provider outage or overload (5xx) after retries: also not a Sales Brain failure.
            log.warning("LLM unavailable for conversation %s: %s", conversation.id, exc)
            turn.meta["llm_error"] = "provider_unavailable"
            return await self._fallback(turn, reason="ai_failure", detail=str(exc)[:200],
                                        llm=llm)  # fmt: skip
        except (LLMError, _NoReply) as exc:
            log.warning("LLM failed for conversation %s: %s", conversation.id, exc)
            return await self._fallback(turn, reason="ai_failure", detail=str(exc)[:200],
                                        llm=llm)  # fmt: skip
        if reply is None:  # every sentence was ungrounded
            return await self._fallback(turn, reason="unsafe_reply", llm=llm)

        reply = await self._enforce_escalation(turn, reply)
        if turn.signals.asked_if_ai or (is_first_reply and conversation.channel != Channel.VOICE):
            reply = f"{turn.scripts.ai_disclosure(turn.language)}\n\n{reply}"
            turn.meta["ai_disclosure"] = True
        self._llm_meta(turn, llm)
        return await self._finish(turn, reply)

    # ----------------------------------------------------------------------- stages

    async def _has_outbound(self, conversation: Conversation) -> bool:
        count = await self.db.scalar(
            select(func.count()).select_from(Message).where(
                Message.conversation_id == conversation.id,
                Message.direction == MessageDirection.OUTBOUND,
            )
        )  # fmt: skip
        return bool(count)

    async def _stopped(self, now: datetime) -> str | None:
        state = await self.db.get(AgentState, 1)
        if state is not None and state.paused:
            return "kill_switch"
        tz = ZoneInfo(self.config.company.timezone)
        month_start = now.astimezone(tz).replace(day=1, hour=0, minute=0, second=0,
                                                 microsecond=0)  # fmt: skip
        spent = await self.db.scalar(
            select(func.coalesce(func.sum(UsageRecord.cost_usd), 0)).where(
                UsageRecord.kind == UsageKind.LLM_TOKENS, UsageRecord.occurred_at >= month_start
            )
        )  # fmt: skip
        if Decimal(spent or 0) >= Decimal(str(self.config.usage_limits.monthly_llm_usd)):
            return "usage_limit"
        return None

    async def _localities(self) -> list[str]:
        rows = await self.db.execute(
            select(Project.locality, Project.city).where(Project.status == RecordStatus.APPROVED)
        )
        names = {n for pair in rows.tuples() for n in pair if n}
        return sorted(names)

    async def _apply_rules(self, turn: _Turn, text: str) -> list[str]:
        found = extract(text, localities=await self._localities())
        if found.requirements.is_empty():
            return []
        merged, changed = await reqs.save(self.db, turn.lead, found.requirements, source="rules")
        memory.remember_requirements(turn.customer, merged.model_dump())
        await advance(self.db, turn.lead, merged, self.config)
        return changed

    async def _record_objection(self, turn: _Turn, text: str) -> None:
        kind = turn.signals.objection
        if kind is None:
            return
        self.db.add(
            Objection(
                lead_id=turn.lead.id,
                conversation_id=turn.conversation.id,
                objection_type=kind,
                text=text[:1000],
                language=turn.language,
            )
        )
        memory.remember_objection(turn.customer, kind)
        await self.db.flush()

    async def _notes(self, turn: _Turn) -> TurnNotes:
        req = await reqs.load(self.db, turn.lead)
        missing = req.missing(list(self.config.lead_scoring.qualified_requires))
        visit = await self.db.scalar(
            select(func.count()).select_from(SiteVisit).where(
                SiteVisit.lead_id == turn.lead.id, SiteVisit.state.in_(HOLDING_STATES)
            )
        )  # fmt: skip
        signals = turn.signals
        return TurnNotes(
            lead_state=turn.lead.state.value,
            qualified=not missing,
            missing=missing,
            known={k: v for k, v in req.model_dump().items() if v not in (None, [])},
            objection=signals.objection,
            objection_script=turn.scripts.objection(turn.language, signals.objection)
            if signals.objection
            else None,
            escalation=signals.escalation,
            language_switched=bool(turn.meta["language"]["switched"]),
            visit_pending=bool(visit),
            unavailable_so_far=await self._unavailable_so_far(turn.conversation),
        )

    async def _unavailable_so_far(self, conversation: Conversation) -> int:
        rows = await self.db.execute(
            select(Message.tool_calls).where(
                Message.conversation_id == conversation.id, Message.tool_calls.is_not(None)
            )
        )
        return sum(
            1
            for (calls,) in rows.tuples()
            for c in calls or []
            if isinstance(c.get("result"), dict) and c["result"].get("status") == "unavailable"
        )

    async def _history_turns(self, turn: _Turn) -> list[Turn]:
        messages = await history(self.db, turn.conversation, limit=HISTORY_LIMIT)
        turns: list[Turn] = []
        for m in messages:
            # Allowed sources for the grounding guard: the customer's words, earlier results.
            if m.direction == MessageDirection.INBOUND:
                turn.grounding.add_text(m.content)
            for call in m.tool_calls or []:
                if call.get("name") not in UNGROUNDING_TOOLS:
                    turn.grounding.add_json(call.get("result"))
            role: Literal["user", "assistant"] = (
                "user" if m.direction == MessageDirection.INBOUND else "assistant"
            )
            if turns and turns[-1].role == role:
                turns[-1].text += "\n" + m.content
            else:
                turns.append(Turn(role, m.content))
        for script in turn.scripts.all_texts(turn.language):
            turn.grounding.add_text(script, plain_numbers=False)
        return turns

    async def _converse(self, turn: _Turn, llm: InstrumentedLLM) -> str | None:
        notes = await self._notes(turn)
        system = build_system_prompt(self.config, turn.scripts, language=turn.language,
                                     channel=turn.conversation.channel, notes=notes)  # fmt: skip
        turns = await self._history_turns(turn)
        ctx = ToolContext(
            db=self.db, config=self.config, clock=self.clock, now=turn.now,
            customer=turn.customer, conversation=turn.conversation, lead=turn.lead,
            language=turn.language, scripts=turn.scripts, embedder=self.embedder,
        )  # fmt: skip
        text, last = await self._run(llm, system, turns, ctx, turn)
        problems = self._problems(turn, text)
        guard: dict[str, Any] = {"first_attempt": problems}
        if problems:
            turns.append(Turn("assistant", text, raw=last.raw))
            turns.append(Turn("user", self._correction(turn, problems)))
            text, last = await self._run(llm, system, turns, ctx, turn)
            problems = self._problems(turn, text)
            guard["second_attempt"] = problems
            if problems.get("ungrounded"):
                text, removed = turn.grounding.remove_ungrounded(text)
                guard["removed_sentences"] = removed
            if problems.get("language"):
                guard["language_mismatch"] = True
        turn.meta["guard"] = guard
        turn.meta["handoffs"] = [str(h.id) for h in ctx.handoffs]
        turn.meta["visit_requested"] = ctx.visit_requested
        return text or None

    async def _run(
        self, llm: InstrumentedLLM, system: str, turns: list[Turn], ctx: ToolContext, turn: _Turn
    ) -> tuple[str, ChatResult]:
        specs = tool_specs()
        for _ in range(MAX_TOOL_ROUNDS):
            result = await llm.chat(
                system=system,
                turns=turns,
                tools=specs,
                temperature=self.config.llm.temperature,
                max_output_tokens=REPLY_MAX_TOKENS,
                purpose="reply",
            )
            if not result.tool_calls:
                if not result.text.strip():
                    raise _NoReply("the model returned no text")
                return result.text.strip(), result
            turns.append(Turn("assistant", result.text, list(result.tool_calls), raw=result.raw))
            results: list[ToolResult] = []
            for call in result.tool_calls:
                content = await run_tool(ctx, call.name, call.arguments)
                turn.tool_log.append(
                    {"name": call.name, "arguments": call.arguments, "result": content}
                )
                if call.name not in UNGROUNDING_TOOLS:
                    turn.grounding.add_json(content)
                results.append(ToolResult(call.id, call.name, content))
            turns.append(Turn("tool", tool_results=results))
        raise _NoReply(f"no answer after {MAX_TOOL_ROUNDS} tool rounds")

    def _problems(self, turn: _Turn, text: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        bad: list[Claim] = turn.grounding.ungrounded(text)
        if bad:
            out["ungrounded"] = [c.as_dict() for c in bad]
        if not language_ok(text, turn.language):
            out["language"] = turn.language
        return out

    @staticmethod
    def _correction(turn: _Turn, problems: dict[str, Any]) -> str:
        parts = ["[Backend check, not from the customer] Rewrite your last reply."]
        if problems.get("ungrounded"):
            items = ", ".join(c["text"] for c in problems["ungrounded"])
            parts.append(
                f"It contains numbers or dates that no tool result gave: {items}. Remove them, "
                "or call the right tool and use its spoken string, or say you will confirm "
                "with the sales team."
            )
        if problems.get("language"):
            from app.brain.prompt import LANGUAGE_BLOCKS

            parts.append(f"It is not in the reply language. {LANGUAGE_BLOCKS.get(turn.language)}")
        parts.append("Reply to the customer only; do not mention this check.")
        return " ".join(parts)

    async def _enforce_escalation(self, turn: _Turn, reply: str) -> str:
        kind = turn.signals.escalation
        if kind == "angry" and await self._angry_before(turn):
            kind = "angry_again"
        if kind not in ALWAYS_HAND_OFF and kind != "angry_again":
            return reply
        if any(c["name"] == "transfer_to_human" for c in turn.tool_log):
            return reply
        reason = "angry" if kind == "angry_again" else str(kind)
        await self._hand_off(turn, reason, trigger=f"escalation enforced: {reason}")
        turn.meta["escalation_enforced"] = reason
        return f"{reply}\n\n{turn.scripts.paused_handoff(turn.language)}"

    async def _angry_before(self, turn: _Turn) -> bool:
        rows = await self.db.execute(
            select(Message.meta).where(
                Message.conversation_id == turn.conversation.id,
                Message.direction == MessageDirection.INBOUND,
                Message.id != turn.inbound.id,
            )
        )
        return any(
            (meta or {}).get("signals", {}).get("escalation") == "angry"
            for (meta,) in rows.tuples()
        )

    async def _hand_off(self, turn: _Turn, reason: str, *, trigger: str,
                        detail: str | None = None) -> None:  # fmt: skip
        handoff = await create_handoff(
            self.db, self.config, lead=turn.lead, customer=turn.customer,
            conversation=turn.conversation, reason=reason, now=turn.now,
            details={"detail": detail} if detail else None,
        )  # fmt: skip
        turn.meta.setdefault("handoffs", []).append(str(handoff.id))
        if turn.lead.state in HAND_OFF_FROM:
            await transition(self.db, turn.lead, LeadState.HANDED_OFF, actor=Actor.SYSTEM,
                             trigger=trigger)  # fmt: skip

    @staticmethod
    def _llm_meta(turn: _Turn, llm: InstrumentedLLM) -> None:
        turn.meta["llm"] = {"provider": llm.provider, "model": llm.model, **llm.stats()}

    async def _fallback(self, turn: _Turn, *, reason: str, detail: str | None = None,
                        llm: InstrumentedLLM | None = None) -> BrainReply:  # fmt: skip
        if llm is not None:
            self._llm_meta(turn, llm)
        await self._hand_off(turn, reason, trigger=reason, detail=detail)
        turn.meta["fallback"] = reason
        if detail:
            turn.meta["fallback_detail"] = detail
        return await self._finish(turn, turn.scripts.paused_handoff(turn.language))

    async def _finish(self, turn: _Turn, text: str) -> BrainReply:
        # Scores come from fixed rules, never from the model: re-score after every turn once
        # anything is known, so the dashboard always shows a current score.
        req = await reqs.load(self.db, turn.lead)
        if not req.is_empty():
            await evaluate(self.db, turn.lead, req, self.config, self.clock, now=turn.now,
                           log_unchanged=False)  # fmt: skip
        message = await record_outbound(
            self.db, conversation=turn.conversation, text=text, language=turn.language,
            now=turn.now, tool_calls=turn.tool_log or None, meta=turn.meta,
        )  # fmt: skip
        handed_off = turn.lead.state == LeadState.HANDED_OFF
        return BrainReply(text, turn.language, message, handed_off, turn.tool_log, turn.meta)
