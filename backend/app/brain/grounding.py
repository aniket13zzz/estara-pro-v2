"""Grounding guard: no number or date in a reply unless a tool result (or the customer) gave it.

The Truth Layer rule says the LLM never produces a number or date on its own. The prompt says
so; this guard checks it. Every amount, area, percentage, clock time, count, duration, date and
any number above 12 in the reply is read with the same reader the extraction uses
(``app.brain.quantities``) and compared by value with everything the turn may quote:

- this conversation's tool results (spoken strings and raw values, list lengths),
- the customer's own messages (repeating "your budget of 80 lakh" back is fine),
- the client's approved script text.

Small plain numbers (1 to 12: "2 BHK", "one question", "3 options" when 3 options came back)
and BHK counts are not claims. Knowledge-base snippets are NOT allowed sources: documents are
never a source of prices or dates (CLAUDE.md), so the tool masks amounts in them.

The brain asks the model once to rewrite a reply that fails; if the rewrite fails too, the
sentences carrying ungrounded claims are removed (``remove_ungrounded``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.brain.quantities import scan, scan_dates, tokens
from app.conversations.language import DEVANAGARI_MIN_RATIO, devanagari_ratio

SMALL_PLAIN = Decimal(12)
DURATION_WORDS = {
    "month", "months", "mahine", "mahina", "mahino", "महीने", "महीना", "महीनों", "माह",
    "year", "years", "saal", "sal", "साल", "वर्ष", "yr", "yrs", "week", "weeks", "hafte",
    "हफ्ते", "हफ़्ते", "day", "days", "din", "दिन",
}  # fmt: skip
ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")


@dataclass(frozen=True)
class Claim:
    kind: str  # money, area, percent, time, count, plain, duration, date
    key: str  # canonical value
    text: str  # the words in the reply

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "value": self.key, "text": self.text}


def _key(value: Decimal) -> str:
    normal = value.normalize()
    return format(normal, "f") if normal == normal.to_integral_value() else str(normal)


def claims(text: str) -> list[Claim]:
    """Every checkable number or date in ``text``."""
    toks = tokens(text)
    out: list[Claim] = []
    for q in scan(text):
        words = " ".join(toks[q.start : q.end])
        after = toks[q.end] if q.end < len(toks) else ""
        if q.kind == "bhk":
            continue
        if q.kind == "plain" and after in DURATION_WORDS:
            out.append(Claim("duration", _key(q.value), f"{words} {after}"))
            continue
        if q.kind == "plain" and q.value <= SMALL_PLAIN and q.value == q.value.to_integral_value():
            continue
        out.append(Claim(q.kind, _key(q.value), words))
    for date_key in sorted(scan_dates(text)):
        out.append(Claim("date", date_key, date_key))
    return out


@dataclass
class Grounding:
    """What the reply may quote. Values are compared regardless of how they were written."""

    values: set[str] = field(default_factory=set)
    dates: set[str] = field(default_factory=set)

    def add_text(self, text: str, *, plain_numbers: bool = True) -> None:
        """``plain_numbers=False`` for script text, where bare numbers are list markers."""
        for q in scan(text):
            if plain_numbers or q.kind != "plain":
                self.values.add(_key(q.value))
        self.dates |= scan_dates(text)
        for match in ISO_DATE.finditer(text):
            year, month, day = match.groups()
            self.dates |= {f"{year}-{month}", f"{month}-{day}"}
            self.values |= {_key(Decimal(year)), _key(Decimal(int(day)))}

    def add_json(self, value: Any) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, int | float | Decimal):
            self.values.add(_key(Decimal(str(value))))
        elif isinstance(value, str):
            self.add_text(value)
        elif isinstance(value, dict):
            for item in value.values():
                self.add_json(item)
        elif isinstance(value, list | tuple):
            self.values.add(_key(Decimal(len(value))))
            for item in value:
                self.add_json(item)

    def ungrounded(self, reply: str) -> list[Claim]:
        bad: list[Claim] = []
        for claim in claims(reply):
            known = self.dates if claim.kind == "date" else self.values
            if claim.key not in known:
                bad.append(claim)
        return bad

    def remove_ungrounded(self, reply: str) -> tuple[str, list[str]]:
        """Remove every sentence that carries an ungrounded claim."""
        kept: list[str] = []
        removed: list[str] = []
        for sentence in SENTENCE_END.split(reply.strip()):
            (removed if self.ungrounded(sentence) else kept).append(sentence)
        return " ".join(kept).strip(), removed


def language_ok(reply: str, language: str) -> bool:
    """Hindi replies are Hinglish in Devanagari; English replies contain no Devanagari.
    Languages without a rule here are not checked."""
    ratio = devanagari_ratio(reply)
    if language == "hi":
        # Hinglish keeps project names, BHK and loanwords in Latin letters, so a correct reply
        # can be mostly Latin by letter count. An English reply has no Devanagari at all.
        return ratio >= DEVANAGARI_MIN_RATIO
    if language == "en":
        return ratio < 0.05
    return True
