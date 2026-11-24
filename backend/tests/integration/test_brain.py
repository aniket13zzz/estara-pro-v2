"""Sales Brain end to end against the sample inventory, with a scripted LLM (no live model).

Covers English, Hindi and Hinglish turns, tool use, the unavailable path, the grounding and
language guards, escalation, objections, the kill switch, usage limits, LLM failure and
site-visit requests.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.scripts import Scripts
from app.brain.service import SalesBrain
from app.conversations.service import get_or_start
from app.core.config import ClientConfig
from app.core.enums import Channel, LeadState, UsageKind, VisitState
from app.db.models import (
    AgentState,
    Conversation,
    Customer,
    Handoff,
    Lead,
    LeadMatch,
    Message,
    Objection,
    SiteVisit,
    UsageRecord,
)
from app.providers.llm import (
    ChatResult,
    LLMRateLimited,
    LLMTransientError,
    RetryPolicy,
    ToolSpec,
    Turn,
)
from app.truth.staleness import FixedClock
from tests.conftest import NOW
from tests.fakes import ScriptedLLM, call, calls, reply

pytestmark = pytest.mark.usefixtures("inventory")
CLOCK = FixedClock(NOW)


async def _conversation(
    db: AsyncSession, config: ClientConfig, channel: Channel = Channel.WEB_CHAT,
    language: str | None = None,
) -> Conversation:  # fmt: skip
    customer = Customer(preferred_language=language)
    db.add(customer)
    await db.flush()
    return await get_or_start(db, customer=customer, channel=channel, config=config, now=NOW)


def _brain(db: AsyncSession, config: ClientConfig, llm: ScriptedLLM | None) -> SalesBrain:
    no_wait = RetryPolicy(base_delay=0, rate_limit_base_delay=0)
    return SalesBrain(db, config, llm=llm, clock=CLOCK, retry=no_wait)


def _results(turns: list[Turn]) -> dict[str, dict[str, Any]]:
    return {r.name: r.content for r in turns[-1].tool_results}


def say_from_tools(build: Callable[[dict[str, dict[str, Any]]], str]) -> Callable[..., ChatResult]:
    """A scripted step that writes its reply from the tool results the brain sent."""

    def step(system: str, turns: list[Turn], tools: list[ToolSpec]) -> ChatResult:
        return reply(build(_results(turns)))

    return step


async def _lead(db: AsyncSession, conversation: Conversation) -> Lead:
    lead = await db.scalar(select(Lead).where(Lead.customer_id == conversation.customer_id))
    assert lead is not None
    return lead


# ------------------------------------------------------------------------- language flows


async def test_english_flow_search_and_slots(db: AsyncSession, client_config: ClientConfig) -> None:
    conv = await _conversation(db, client_config, language="en")

    def answer(r: dict[str, dict[str, Any]]) -> str:
        slots = r["get_visit_slots"]["slots"]
        return (
            f"I can request a visit on {slots[0]['slot']} or {slots[1]['slot']}. Which suits you?"
        )

    def first_match(system: str, turns: list[Turn], tools: list[ToolSpec]) -> ChatResult:
        match = _results(turns)["search_properties"]["matches"][0]
        return call("get_visit_slots", project_code=match["project_code"])

    llm = ScriptedLLM([call("search_properties"), first_match, say_from_tools(answer)])
    out = await _brain(db, client_config, llm).respond(
        conv, "I'm looking for a 2 BHK in New Town, budget around 80 lakh"
    )
    scripts = Scripts(client_config)
    assert out.language == "en"
    assert out.text.startswith(scripts.ai_disclosure("en"))  # first reply carries the disclosure
    assert "Saturday 17 October at 11 am" in out.text
    assert out.meta["guard"]["first_attempt"] == {}
    lead = await _lead(db, conv)
    assert lead.state == LeadState.QUALIFIED  # rules read budget, location, BHK (→ apartment)
    assert await db.scalar(select(func.count()).select_from(LeadMatch)) >= 1
    usage = (await db.execute(select(UsageRecord))).scalars().all()
    assert len(usage) == 3 and all(u.kind == UsageKind.LLM_TOKENS for u in usage)
    assert all(u.cost_usd is not None and u.details["purpose"] == "reply" for u in usage)
    # The prompt tells the model the lead is qualified and to offer a visit.
    assert "offer a site visit" in llm.requests[0].system
    assert [t["name"] for t in out.tool_calls] == ["search_properties", "get_visit_slots"]


async def test_hindi_flow_replies_in_devanagari(
    db: AsyncSession, client_config: ClientConfig
) -> None:
    conv = await _conversation(db, client_config, language="en")

    def answer(r: dict[str, dict[str, Any]]) -> str:
        m = r["search_properties"]["matches"][0]
        return (
            f"{m['project']} में {m['bhk']} BHK का option है, कीमत {m['price']}। "
            "क्या आप site visit करना चाहेंगे?"
        )

    llm = ScriptedLLM([call("search_properties"), say_from_tools(answer)])
    out = await _brain(db, client_config, llm).respond(
        conv, "मुझे Behala में दो BHK फ्लैट चाहिए, बजट सत्तर लाख तक"
    )
    assert out.language == "hi"
    assert "अड़सठ लाख" in out.text  # the Hindi spoken string from the Truth Layer
    assert out.meta["language"]["switched"] is True
    assert out.meta["guard"]["first_attempt"] == {}
    assert "Hinglish written in Devanagari" in llm.requests[0].system
    customer = await db.get(Customer, conv.customer_id)
    assert customer is not None and customer.preferred_language == "hi"


async def test_hinglish_is_hindi_and_budget_is_read_by_rules(
    db: AsyncSession, client_config: ClientConfig
) -> None:
    conv = await _conversation(db, client_config, language="hi")
    llm = ScriptedLLM([reply("ठीक है, आपको कौन सा area पसंद है?")])
    out = await _brain(db, client_config, llm).respond(conv, "budget ek se dedh crore hai, 3 bhk")
    assert out.language == "hi"
    inbound = (await db.execute(select(Message).where(Message.direction == "inbound"))).scalar_one()
    assert set(inbound.meta["extracted"]) >= {"budget_min_inr", "budget_max_inr", "bhk"}
    assert '"budget_min_inr": 10000000' in llm.requests[0].system


# ---------------------------------------------------------------------- truth and guards


async def test_unavailable_fact_is_confirmed_with_team(
    db: AsyncSession, client_config: ClientConfig
) -> None:
    conv = await _conversation(db, client_config, language="en")
    llm = ScriptedLLM([
        call("get_commercial_info", project_code="SH", fact_type="offer"),
        reply("Let me confirm the current offers with our sales team and get back to you."),
    ])  # fmt: skip
    out = await _brain(db, client_config, llm).respond(conv, "Any offer on Sunrise Heights?")
    result = out.tool_calls[0]["result"]
    assert result["status"] == "unavailable" and result["reason"] == "missing"
    assert "confirm" in result["instruction"]
    assert "confirm" in out.text and out.meta["guard"]["first_attempt"] == {}


async def test_ungrounded_number_is_sent_back_then_stripped(
    db: AsyncSession, client_config: ClientConfig
) -> None:
    conv = await _conversation(db, client_config, language="en")
    bad = "Sunrise Greens is lovely. A 2 BHK there is 75 lakh rupees. Shall I share visit slots?"
    llm = ScriptedLLM([reply(bad), reply(bad)])
    out = await _brain(db, client_config, llm).respond(
        conv, "How much is a 2 BHK in Sunrise Greens"
    )
    assert "Backend check" in llm.requests[1].turns[-1].text
    assert "75 lakh rupees" in llm.requests[1].turns[-1].text
    guard = out.meta["guard"]
    assert guard["first_attempt"]["ungrounded"][0]["value"] == "7500000"
    assert guard["removed_sentences"] == ["A 2 BHK there is 75 lakh rupees."]
    assert "75 lakh" not in out.text
    assert out.text.endswith("Sunrise Greens is lovely. Shall I share visit slots?")


async def test_ungrounded_reply_fixed_on_rewrite(
    db: AsyncSession, client_config: ClientConfig
) -> None:
    conv = await _conversation(db, client_config, language="en")
    llm = ScriptedLLM([
        reply("Possession is in March 2027."),
        call("get_commercial_info", project_code="SG", fact_type="possession"),
        say_from_tools(lambda r: f"Possession is planned for {r['get_commercial_info']['say']}."),
    ])  # fmt: skip
    out = await _brain(db, client_config, llm).respond(
        conv, "When is possession at Sunrise Greens?"
    )
    assert out.text.endswith("Possession is planned for December 2027.")
    assert "second_attempt" in out.meta["guard"] and out.meta["guard"]["second_attempt"] == {}


async def test_only_ungrounded_content_hands_off(
    db: AsyncSession, client_config: ClientConfig
) -> None:
    conv = await _conversation(db, client_config, language="en")
    llm = ScriptedLLM([reply("It is 5% off."), reply("It is 5% off.")])
    out = await _brain(db, client_config, llm).respond(conv, "Is there a discount")
    assert out.meta["fallback"] == "unsafe_reply"
    assert out.text == Scripts(client_config).paused_handoff("en")
    assert (await db.execute(select(Handoff.reason))).scalars().all() == ["unsafe_reply"]


async def test_wrong_language_is_rewritten(db: AsyncSession, client_config: ClientConfig) -> None:
    conv = await _conversation(db, client_config, language="hi")
    llm = ScriptedLLM(
        [reply("Sure, which area do you prefer?"), reply("ज़रूर, आपको कौन सा area पसंद है?")]
    )
    out = await _brain(db, client_config, llm).respond(conv, "मुझे घर चाहिए")
    assert out.meta["guard"]["first_attempt"] == {"language": "hi"}
    assert out.text.endswith("ज़रूर, आपको कौन सा area पसंद है?")


# ----------------------------------------------------------------- escalation and signals


async def test_request_for_a_person_always_hands_off(
    db: AsyncSession, client_config: ClientConfig
) -> None:
    conv = await _conversation(db, client_config, language="en")
    llm = ScriptedLLM([reply("Of course.")])  # the model forgot to call transfer_to_human
    out = await _brain(db, client_config, llm).respond(conv, "I want to talk to a real person")
    assert out.handed_off and out.meta["escalation_enforced"] == "human_requested"
    assert out.text.endswith(Scripts(client_config).paused_handoff("en"))
    handoff = (await db.execute(select(Handoff))).scalar_one()
    assert handoff.reason == "human_requested" and handoff.assigned_email == "rahul@example.com"
    assert handoff.context["recent_messages"][-1]["text"] == "I want to talk to a real person"
    assert "Call the customer back" in handoff.context["recommended_next_action"]
    assert (await _lead(db, conv)).state == LeadState.HANDED_OFF


async def test_transfer_tool_is_not_doubled(db: AsyncSession, client_config: ClientConfig) -> None:
    conv = await _conversation(db, client_config, language="hi")
    llm = ScriptedLLM([
        call("transfer_to_human", reason="legal"),
        reply("मैं आपको हमारी sales team से connect कर रही हूँ, वे आपसे जल्दी बात करेंगे।"),
    ])  # fmt: skip
    out = await _brain(db, client_config, llm).respond(conv, "मुझे वकील से बात करनी है, कानूनी सवाल है")
    assert "escalation_enforced" not in out.meta
    assert (await db.execute(select(Handoff.reason))).scalars().all() == ["legal"]
    assert out.tool_calls[0]["result"]["within_working_hours"] is True


async def test_objection_is_recorded_and_its_script_is_used(
    db: AsyncSession, client_config: ClientConfig
) -> None:
    conv = await _conversation(db, client_config, language="hi")
    llm = ScriptedLLM([reply("समझ सकती हूँ, मैं आपकी range के आस-पास options देखती हूँ।")])
    await _brain(db, client_config, llm).respond(conv, "ye to bahut mehenga hai")
    objection = (await db.execute(select(Objection))).scalar_one()
    assert objection.objection_type == "price" and objection.language == "hi"
    assert Scripts(client_config).objection("hi", "price") in llm.requests[0].system
    customer = await db.get(Customer, conv.customer_id)
    assert customer is not None and customer.memory["objections"] == ["price"]


async def test_asking_if_ai_gets_the_disclosure(
    db: AsyncSession, client_config: ClientConfig
) -> None:
    conv = await _conversation(db, client_config, language="en")
    llm = ScriptedLLM([reply("Hi! What are you looking for?"), reply("Yes, I'm an AI assistant.")])
    brain = _brain(db, client_config, llm)
    first = await brain.respond(conv, "hello")
    second = await brain.respond(conv, "are you a bot?")
    disclosure = Scripts(client_config).ai_disclosure("en")
    assert first.text.startswith(disclosure) and second.text.startswith(disclosure)


# ------------------------------------------------------------------ stops and failures


async def test_kill_switch_skips_the_llm(db: AsyncSession, client_config: ClientConfig) -> None:
    db.add(AgentState(id=1, paused=True, paused_reason="test"))
    await db.flush()
    conv = await _conversation(db, client_config, language="hi")
    llm = ScriptedLLM([])
    out = await _brain(db, client_config, llm).respond(conv, "नमस्ते")
    assert llm.requests == []
    assert out.text == Scripts(client_config).paused_handoff("hi")
    assert out.meta["fallback"] == "kill_switch" and out.handed_off


async def test_usage_limit_skips_the_llm(db: AsyncSession, client_config: ClientConfig) -> None:
    limit = Decimal(str(client_config.usage_limits.monthly_llm_usd))
    db.add(UsageRecord(kind=UsageKind.LLM_TOKENS, provider="gemini", quantity=Decimal(1),
                       unit="tokens", cost_usd=limit, occurred_at=NOW))  # fmt: skip
    await db.flush()
    conv = await _conversation(db, client_config, language="en")
    out = await _brain(db, client_config, ScriptedLLM([])).respond(conv, "hello there friend")
    assert out.meta["fallback"] == "usage_limit"


async def test_llm_failure_falls_back_after_retries(
    db: AsyncSession, client_config: ClientConfig
) -> None:
    conv = await _conversation(db, client_config, language="en")
    llm = ScriptedLLM([LLMTransientError("503")] * 3)
    out = await _brain(db, client_config, llm).respond(conv, "Do you have 3 BHK flats?")
    assert len(llm.requests) == 3
    assert out.meta["fallback"] == "ai_failure"
    assert out.text == Scripts(client_config).paused_handoff("en")
    assert (await db.execute(select(Handoff.reason))).scalars().all() == ["ai_failure"]


async def test_rate_limit_is_marked_apart_from_brain_failures(
    db: AsyncSession, client_config: ClientConfig
) -> None:
    conv = await _conversation(db, client_config, language="en")
    llm = ScriptedLLM([LLMRateLimited("429", retry_after=0)] * 3)
    out = await _brain(db, client_config, llm).respond(conv, "Do you have 3 BHK flats?")
    assert len(llm.requests) == 3  # rate_limit_attempts
    assert out.meta["llm_error"] == "rate_limited" and out.meta["fallback"] == "ai_failure"
    daily = ScriptedLLM([LLMRateLimited("429 per day", daily=True)])
    conv2 = await _conversation(db, client_config, language="en")
    out2 = await _brain(db, client_config, daily).respond(conv2, "Do you have 2 BHK flats?")
    assert len(daily.requests) == 1 and out2.meta["llm_error"] == "rate_limited_daily"


async def test_bad_tool_calls_return_structured_errors(
    db: AsyncSession, client_config: ClientConfig
) -> None:
    conv = await _conversation(db, client_config, language="en")
    llm = ScriptedLLM([
        calls(("no_such_tool", {}), ("get_commercial_info", {"project_code": "SG",
                                                              "fact_type": "colour"})),
        reply("Let me confirm that with our sales team."),
    ])  # fmt: skip
    out = await _brain(db, client_config, llm).respond(conv, "what colour are the walls")
    errors = [t["result"]["status"] for t in out.tool_calls]
    assert errors == ["error", "error"]
    assert "invalid arguments" in out.tool_calls[1]["result"]["error"]


# ----------------------------------------------------------------------------- visits


async def test_visit_request_needs_qualification(
    db: AsyncSession, client_config: ClientConfig
) -> None:
    conv = await _conversation(db, client_config, language="en")
    llm = ScriptedLLM([
        call("request_site_visit", project_code="SG", slot_id="2026-10-17T11:00",
             visitor_name="Asha"),
        reply("Before I request a visit, which area do you prefer?"),
    ])  # fmt: skip
    out = await _brain(db, client_config, llm).respond(conv, "Can I visit this weekend?")
    assert out.tool_calls[0]["result"]["status"] == "needs_requirements"
    assert await db.scalar(select(func.count()).select_from(SiteVisit)) == 0


async def test_visit_request_creates_visit_and_handoff(
    db: AsyncSession, client_config: ClientConfig
) -> None:
    conv = await _conversation(db, client_config, language="en")
    brain = _brain(db, client_config, ScriptedLLM([reply("Great. What name should I use?")]))
    await brain.respond(conv, "2 BHK in New Town under 80 lakh")

    def request(system: str, turns: list[Turn], tools: list[ToolSpec]) -> ChatResult:
        slot = _results(turns)["get_visit_slots"]["slots"][0]["slot_id"]
        return call("request_site_visit", project_code="SG", slot_id=slot, visitor_name="Asha")

    brain.llm = ScriptedLLM([
        call("get_visit_slots", project_code="SG"), request,
        say_from_tools(lambda r: f"Done, I've requested {r['request_site_visit']['slot']}. "
                       "The sales team will confirm it."),
    ])  # fmt: skip
    out = await brain.respond(conv, "I'm Asha, the first slot is fine")
    visit = (await db.execute(select(SiteVisit))).scalar_one()
    assert visit.state == VisitState.REQUESTED and visit.visitor_name == "Asha"
    lead = await _lead(db, conv)
    assert lead.state == LeadState.VISIT_REQUESTED
    handoff = (await db.execute(select(Handoff))).scalar_one()
    assert handoff.reason == "visit_requested"
    assert "Confirm or reschedule" in handoff.context["recommended_next_action"]
    assert not out.handed_off  # the lead stays visit_requested until staff accept (ADR 0007)
    assert "Saturday 17 October at 11 am" in out.text
    assert not out.text.startswith(Scripts(client_config).ai_disclosure("en"))


async def test_follow_up_moves_lead_to_nurturing(
    db: AsyncSession, client_config: ClientConfig
) -> None:
    conv = await _conversation(db, client_config, language="en")
    llm = ScriptedLLM([
        call("schedule_follow_up", days_from_now=3),
        reply("No problem, our team will follow up with you on WhatsApp."),
    ])  # fmt: skip
    out = await _brain(db, client_config, llm).respond(
        conv, "I want a 2 BHK but not now, maybe later"
    )
    assert out.tool_calls[0]["result"]["status"] == "scheduled"
    assert (await _lead(db, conv)).state == LeadState.NURTURING
