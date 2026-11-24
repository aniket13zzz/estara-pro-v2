"""v1.4.0, a more natural conversation: real availability with each match, visitor count on a
visit request, loan and builder-trust objections, and the conversation rules in the prompt."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.prompt import RULES
from app.brain.scripts import Scripts
from app.brain.signals import detect_signals
from app.channels.vapi.native import native_prompt
from app.core.config import ClientConfig
from app.db.models import Handoff, Lead, SiteVisit
from app.leads import requirements as reqs
from tests.integration.test_native_voice import native, result_of, tool_request, use  # noqa: F401

SAID = [("user", "I want a 2 BHK flat in New Town, budget up to 80 lakh")]


@pytest.mark.usefixtures("inventory")
async def test_matches_say_how_many_homes_are_available(
    native: tuple[FastAPI, AsyncClient],  # noqa: F811
) -> None:
    _, client = native
    args = {"locations": ["New Town"], "bhk": [2], "budget_text": "80 lakh"}
    [out] = await use(client, tool_request(("t1", "search_properties", args), said=SAID))
    matches = result_of(out)["matches"]
    assert matches
    for match in matches:
        assert match["availability"].endswith("available")  # a Truth Layer string, not a guess


@pytest.mark.usefixtures("inventory")
async def test_visit_request_keeps_how_many_people_will_come(
    native: tuple[FastAPI, AsyncClient],  # noqa: F811
    db: AsyncSession,
) -> None:
    _, client = native
    [slots] = await use(client, tool_request(("t1", "get_visit_slots", {"project_code": "SG"}),
                                             said=SAID))  # fmt: skip
    slot_id = result_of(slots)["slots"][0]["slot_id"]
    visit = {"project_code": "SG", "slot_id": slot_id, "visitor_name": "Asha", "visitors": 3}
    [done] = await use(client, tool_request(("t2", "request_site_visit", visit), said=SAID))
    assert result_of(done)["status"] == "requested"
    row = (await db.execute(select(SiteVisit))).scalar_one()
    assert row.notes == "Visitors: 3"
    handoff = (await db.execute(select(Handoff))).scalar_one()
    assert handoff.context["details"]["visitors"] == 3


@pytest.mark.parametrize(("text", "kind"), [
    ("Can I get a home loan for this?", "loan"),
    ("EMI kitni hogi, loan milega?", "loan"),
    ("मुझे होम लोन चाहिए", "loan"),
    ("What is the builder's track record?", "trust"),
    ("Construction quality kaisi hai, bharosa kaise karein?", "trust"),
    ("This is too expensive for me", "price"),
])  # fmt: skip
def test_loan_and_trust_objections_are_detected(text: str, kind: str) -> None:
    assert detect_signals(text).objection == kind


def test_premium_is_not_an_emi_question() -> None:
    assert detect_signals("Show me something premium").objection is None


def test_prompt_has_the_conversation_rules_and_new_scripts(client_config: ClientConfig) -> None:
    for rule in ("use all of them and ask only for", "never\n  restart the conversation",
                 "the second one", "which one fits them better", "how many people will come",
                 "Never end a call without a next step", "Never add pressure words",
                 "Never repeat back or summarise",
                 "A missing detail is not a reason to hand"):  # fmt: skip
        assert rule in RULES, rule
    scripts = Scripts(client_config)
    prompt = native_prompt(client_config, scripts)
    for lang in client_config.agent.languages:
        for kind in ("loan", "trust"):
            assert scripts.objection(lang, kind) in prompt


def test_prompt_handles_misheard_sizes(client_config: ClientConfig) -> None:
    prompt = native_prompt(client_config, Scripts(client_config))
    assert '"C BHK"' in prompt and "3 BHK, right?" in prompt  # live call: "3 BHK" heard as C


@pytest.mark.usefixtures("inventory")
async def test_search_saves_the_need_and_labels_other_areas(
    native: tuple[FastAPI, AsyncClient],  # noqa: F811
    db: AsyncSession,
) -> None:
    """The brief's example: a size the asked-for area does not have. Search must return the
    real options elsewhere, labelled, and save what was asked (no separate save call)."""
    _, client = native
    args = {"locations": ["Howrah"], "bhk": [3], "budget_text": "1 crore"}
    [out] = await use(client, tool_request(("t1", "search_properties", args), said=SAID))
    result = result_of(out)
    assert result["matches"] and result["exact_match"] is False
    assert all("location" in m["differs"] for m in result["matches"])  # no 3 BHK in Howrah
    assert "Never call an alternative a match" in result["instruction"]
    lead = (await db.execute(select(Lead))).scalar_one()
    saved = await reqs.load(db, lead)
    assert saved.bhk == [3] and saved.locations == ["Howrah"] and saved.budget_max_inr


@pytest.mark.usefixtures("inventory")
async def test_an_exact_match_is_presented_as_one(
    native: tuple[FastAPI, AsyncClient],  # noqa: F811
) -> None:
    _, client = native
    args = {"locations": ["New Town"], "bhk": [2], "budget_text": "80 lakh"}
    [out] = await use(client, tool_request(("t1", "search_properties", args), said=SAID))
    result = result_of(out)
    assert result["exact_match"] is True and "differs" not in result["matches"][0]
    assert "recommend" in result["instruction"]
