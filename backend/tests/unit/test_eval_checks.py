"""The eval set and its checkers (the checks must fail on bad replies, not only pass good ones)."""

from __future__ import annotations

from collections import Counter
from typing import Any

from tests.evals.checks import (
    EvalTurn,
    Expectation,
    check_handoff,
    check_language,
    check_numbers,
    check_unavailable,
    check_visit_offered,
    guard_interventions,
)
from tests.evals.run import load_conversations

SLOTS = {"name": "get_visit_slots", "arguments": {"project_code": "SG"},
         "result": {"slots": [{"slot_id": "2026-10-17T11:00",
                               "slot": "Saturday 17 October at 11 am"}]}}  # fmt: skip
PRICE_SAID = "seventy two to seventy eight lakh rupees"
PRICE = {"name": "search_properties", "arguments": {},
         "result": {"matches": [{"price": PRICE_SAID}]}}  # fmt: skip
OFFER_MISSING = {"name": "get_commercial_info", "arguments": {},
                 "result": {"status": "unavailable", "reason": "missing"}}  # fmt: skip


def turn(reply: str, *, tools: list[dict[str, Any]] | None = None, state: str = "engaged",
         lang: str = "en", customer: str = "hello there") -> EvalTurn:  # fmt: skip
    return EvalTurn(customer, lang, reply, lang, tools or [], {}, state)


def test_the_set_has_ten_sales_and_one_privacy_conversation_per_style() -> None:
    conversations = load_conversations()
    assert Counter(c.style for c in conversations) == {"en": 11, "hi": 11, "hinglish": 11}
    assert len({c.id for c in conversations}) == 33
    privacy = [c for c in conversations if c.id.startswith("sec_")]
    assert sorted(c.style for c in privacy) == ["en", "hi", "hinglish"]
    assert all(c.turns and all(t for t, _ in c.turns) for c in conversations)
    assert sum(c.expect.qualified for c in conversations) >= 15
    assert sum(c.expect.unavailable for c in conversations) >= 6
    assert {c.expect.handoff for c in conversations} >= {"human_requested", "legal"}
    assert all(lang in {"en", "hi"} for c in conversations for _, lang in c.turns)


def test_numbers_check_accepts_tool_numbers_and_rejects_invented_ones() -> None:
    ok = [turn("The 2 BHK is 72 to 78 lakh rupees.", tools=[PRICE])]
    assert check_numbers(ok, scripts=[]).passed
    words = [turn("It is about seventy five lakh rupees.", tools=[PRICE])]
    assert not check_numbers(words, scripts=[]).passed
    digits = [turn("Call us on 98300 12345.", tools=[PRICE])]
    result = check_numbers(digits, scripts=[])
    assert not result.passed and "98300" in result.detail
    customer_said = [turn("Noted, your budget is 80 lakh.", customer="budget 80 lakh")]
    assert check_numbers(customer_said, scripts=[]).passed


def test_language_check() -> None:
    assert check_language([turn("Which area do you prefer?")]).passed
    assert not check_language([turn("Which area do you prefer?", lang="hi")]).passed
    hindi = EvalTurn("x", "hi", "आपको कौन सा area पसंद है?", "hi")
    assert check_language([hindi]).passed


def test_visit_offered_needs_qualification_and_returned_slots() -> None:
    expect = Expectation(qualified=True)
    never = check_visit_offered([turn("What budget?")], expect)
    assert never is not None and not never.passed and "never qualified" in never.detail
    no_slot = check_visit_offered([turn("Great, anything else?", state="qualified")], expect)
    assert no_slot is not None and not no_slot.passed
    offered = check_visit_offered(
        [turn("How about Saturday 17 October at 11 am?", tools=[SLOTS], state="qualified")],
        expect,
    )
    assert offered is not None and offered.passed
    assert check_visit_offered([turn("hi")], Expectation()) is None


def test_unavailable_must_be_said_as_confirm_with_team() -> None:
    expect = Expectation(unavailable=True)
    good = check_unavailable(
        [turn("I'll confirm the offer with our sales team.", tools=[OFFER_MISSING])], expect
    )
    assert good is not None and good.passed
    dodged = check_unavailable([turn("No offers right now.", tools=[OFFER_MISSING])], expect)
    assert dodged is not None and not dodged.passed
    hindi = EvalTurn("x", "hi", "मैं team से confirm करके बताती हूँ।", "hi", [OFFER_MISSING])
    result = check_unavailable([hindi], expect)
    assert result is not None and result.passed


def test_handoff_and_guard_counts() -> None:
    result = check_handoff(["legal"], Expectation(handoff="human_requested"))
    assert result is not None and not result.passed
    t = turn("ok")
    t.meta = {"guard": {"first_attempt": {"ungrounded": [{}], "language": "hi"},
                        "removed_sentences": ["x"]}}  # fmt: skip
    assert guard_interventions([t]) == {"draft_ungrounded": 1, "draft_wrong_language": 1,
                                        "sentences_removed": 1}  # fmt: skip
