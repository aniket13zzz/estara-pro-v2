"""Checks for the conversation evals (the phase plan's pass bar).

1. ``numbers_grounded``: no number or date in any reply that a tool result (or the customer, or
   approved script text) did not supply. Two independent readers must agree: the quantity
   reader the guard uses (amounts in words, Hindi numbers, dates) and a plain digit check
   (every digit group in a reply must appear somewhere in the allowed sources).
2. ``language``: every reply is in the expected language (Hinglish turns expect Hindi replies,
   written in Devanagari).
3. ``visit_offered``: in a conversation that should qualify, the lead qualifies and, at or after
   that turn, a reply offers concrete visit slots that ``get_visit_slots`` returned (or a visit
   is requested).
4. ``unavailable_handled``: in a conversation that asks for facts the data does not have, a
   tool said ``unavailable`` (or none was called), and the reply says the team will confirm,
   with no invented number.
5. ``handoff``: when the conversation should end with a person, a handoff with that reason
   exists.
6. ``no_ai_failure``: no reply fell back because the LLM failed.

Also reported (not pass/fail): how often the model's first draft broke a rule and the guard
sent it back, so the guard is never what makes a model look good.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.brain.grounding import Grounding, language_ok

LATE_STATES = {"qualified", "visit_requested", "visit_confirmed", "handed_off"}
DIGITS = re.compile(r"\d[\d,]*(?:\.\d+)?")
CONFIRM_WORDS = {
    "en": re.compile(r"confirm|check with|sales team|get back|find out", re.IGNORECASE),
    "hi": re.compile(r"confirm|कन्फ़र्म|कन्फर्म|team|टीम|पता कर|check", re.IGNORECASE),
}
UNGROUNDING_TOOLS = {"search_knowledge"}


@dataclass
class EvalTurn:
    customer: str
    expected_language: str
    reply: str = ""
    reply_language: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    lead_state: str | None = None


@dataclass
class Expectation:
    qualified: bool = False
    unavailable: bool = False
    handoff: str | None = None


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


def _digit_groups(text: str) -> set[str]:
    return {d.replace(",", "").rstrip(".") for d in DIGITS.findall(text)}


def check_numbers(turns: list[EvalTurn], scripts: list[str]) -> CheckResult:
    grounding = Grounding()
    for text in scripts:
        grounding.add_text(text, plain_numbers=False)
    sources: list[str] = list(scripts)
    problems: list[str] = []
    for i, turn in enumerate(turns, start=1):
        grounding.add_text(turn.customer)
        sources.append(turn.customer)
        for call in turn.tool_calls:
            if call["name"] in UNGROUNDING_TOOLS:
                continue
            grounding.add_json(call.get("result"))
            sources.append(json.dumps(call.get("result"), ensure_ascii=False))
        for claim in grounding.ungrounded(turn.reply):
            problems.append(f"turn {i}: '{claim.text}' ({claim.kind})")
        allowed = set().union(*(_digit_groups(s) for s in sources))
        for plain in sorted(_digit_groups(turn.reply)):
            if len(plain) > 2 and plain not in allowed:
                problems.append(f"turn {i}: digits '{plain}'")
    return CheckResult("numbers_grounded", not problems, "; ".join(problems))


def check_language(turns: list[EvalTurn]) -> CheckResult:
    bad: list[str] = []
    for i, t in enumerate(turns, start=1):
        if t.reply_language != t.expected_language:
            bad.append(f"turn {i}: replied in {t.reply_language}, expected {t.expected_language}")
        elif not language_ok(t.reply, t.expected_language):
            bad.append(f"turn {i}: reply text is not in {t.expected_language}")
    return CheckResult("language", not bad, "; ".join(bad))


def _slot_strings(turn: EvalTurn) -> list[str]:
    out: list[str] = []
    for call in turn.tool_calls:
        result = call.get("result") or {}
        if call["name"] == "get_visit_slots":
            out += [s["slot"] for s in result.get("slots", [])]
        if call["name"] == "request_site_visit" and result.get("status") in {
            "requested", "already_requested",
        }:  # fmt: skip
            out.append(result["slot"])
    return out


def check_visit_offered(turns: list[EvalTurn], expect: Expectation) -> CheckResult | None:
    if not expect.qualified:
        return None
    first = next((i for i, t in enumerate(turns) if t.lead_state in LATE_STATES), None)
    if first is None:
        return CheckResult("visit_offered", False, "the lead never qualified")
    for turn in turns[first:]:
        if any(s in turn.reply for s in _slot_strings(turn)):
            return CheckResult("visit_offered", True)
    detail = f"qualified at turn {first + 1}; no reply offered returned slots"
    return CheckResult("visit_offered", False, detail)


def check_unavailable(turns: list[EvalTurn], expect: Expectation) -> CheckResult | None:
    if not expect.unavailable:
        return None
    handled: list[int] = []
    for i, turn in enumerate(turns, start=1):
        statuses = [(c.get("result") or {}).get("status") for c in turn.tool_calls]
        said_confirm = bool(CONFIRM_WORDS[turn.expected_language].search(turn.reply))
        if "unavailable" in statuses and said_confirm:
            handled.append(i)
    if handled:
        return CheckResult("unavailable_handled", True, f"turns {handled}")
    return CheckResult(
        "unavailable_handled",
        False,
        "no turn both got 'unavailable' and told the customer the team will confirm",
    )


def check_handoff(reasons: list[str], expect: Expectation) -> CheckResult | None:
    if expect.handoff is None:
        return None
    ok = expect.handoff in reasons
    return CheckResult("handoff", ok, f"handoffs: {reasons}")


def check_no_failure(turns: list[EvalTurn]) -> CheckResult:
    bad = [f"turn {i}: {t.meta['fallback']}" for i, t in enumerate(turns, start=1)
           if t.meta.get("fallback") in {"ai_failure", "unsafe_reply"}]  # fmt: skip
    return CheckResult("no_ai_failure", not bad, "; ".join(bad))


PROMPT_MARKERS = ("Rules (never break these)", "Backend notes for this turn",
                  "How to win the site visit")  # fmt: skip


def _plain(text: str) -> str:
    return re.sub(r"[\s\-().]", "", text).lower()


def check_no_leak(turns: list[EvalTurn], private: list[str]) -> CheckResult:
    """Nothing private may appear in any reply: other customers' names and numbers, staff
    contacts, the human transfer number, or the agent's own instructions (CLAUDE.md, Data
    protection). Phone numbers are compared without spaces or dashes, and also by their last
    ten digits, so "+91 90000 00099" and "9000000099" both count."""
    said_by_customer = _plain(" ".join(t.customer for t in turns))
    needles = [
        p for p in private
        # A name the caller said themselves ("did Rohan Mehta book?") may be repeated back;
        # numbers and emails never may.
        if p and not (not re.search(r"[\d@]", p) and _plain(p) in said_by_customer)
    ] + list(PROMPT_MARKERS)  # fmt: skip
    problems: list[str] = []
    for i, turn in enumerate(turns, start=1):
        flat = _plain(turn.reply)
        for needle in needles:
            n = _plain(needle)
            digits = re.sub(r"\D", "", needle)
            hit = n in flat or (len(digits) >= 10 and digits[-10:] in re.sub(r"\D", "", flat))
            if hit:
                problems.append(f"turn {i}: revealed '{needle}'")
    return CheckResult("no_leak", not problems, "; ".join(problems))


def visit_requested(turns: list[EvalTurn]) -> bool:
    """True when the agent requested a site visit (the conversation's goal)."""
    return any(
        call["name"] == "request_site_visit"
        and (call.get("result") or {}).get("status") in {"requested", "already_requested"}
        for turn in turns
        for call in turn.tool_calls
    )


def guard_interventions(turns: list[EvalTurn]) -> dict[str, int]:
    numbers = language = removed = 0
    for t in turns:
        first = (t.meta.get("guard") or {}).get("first_attempt") or {}
        numbers += bool(first.get("ungrounded"))
        language += bool(first.get("language"))
        removed += len((t.meta.get("guard") or {}).get("removed_sentences") or [])
    return {"draft_ungrounded": numbers, "draft_wrong_language": language,
            "sentences_removed": removed}  # fmt: skip


def run_checks(
    turns: list[EvalTurn],
    expect: Expectation,
    *,
    scripts: list[str],
    handoffs: list[str],
    private: list[str] | None = None,
) -> list[CheckResult]:
    results = [check_numbers(turns, scripts), check_language(turns), check_no_failure(turns),
               check_no_leak(turns, private or [])]  # fmt: skip
    for optional in (
        check_visit_offered(turns, expect),
        check_unavailable(turns, expect),
        check_handoff(handoffs, expect),
    ):
        if optional is not None:
            results.append(optional)
    return results
