"""Deterministic requirement extraction from a customer message (en, hi, Hinglish).

Budgets, BHK, property type, timeline, loan and purpose are read by rules, never by the LLM,
so "ek se dedh crore" always means ₹1,00,00,000 to ₹1,50,00,000. Locations are matched against
the localities and cities of approved projects; places the rules don't know (or written in
Devanagari) are passed by the LLM to ``save_requirements`` and checked there.

Budget rules:

- An amount needs a money unit (lakh, crore, rupees, ₹) or the word budget nearby, so "2 BHK"
  or "12th floor" are not budgets.
- "X to Y" / "X se Y" / "X से Y" is a range. A single amount is the upper limit, as are
  "tak", "under", "within", "up to", "max", "से कम". "above", "at least", "minimum",
  "se zyada", "से ऊपर", "plus" make it the lower limit, unless negated ("se zyada nahi").
- If several amounts appear, the one nearest the word "budget" wins, else the first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.brain.quantities import Quantity, nfc, read_number, scan, tokens
from app.leads.requirements import Requirements

BUDGET_WORDS = {"budget", "bajat", "budjet", "बजट", "range"}
UPPER_BEFORE = {"under", "below", "within", "upto", "max", "maximum", "less"}
UPPER_AFTER = {"tak", "तक", "max", "maximum", "andar", "अंदर", "kam", "कम"}
UPPER_PHRASES = ("up to", "not more than", "less than", "se kam", "से कम", "ke andar", "के अंदर")
LOWER_BEFORE = {"above", "minimum", "min", "atleast", "over"}
LOWER_AFTER = {"plus", "upar", "ऊपर", "zyada", "jyada", "ज़्यादा", "ज्यादा", "above"}
LOWER_PHRASES = (
    "at least", "more than", "se zyada", "se upar", "से ज़्यादा", "से ज्यादा", "से ऊपर",
)  # fmt: skip
NEGATIONS = {"nahi", "nahin", "nai", "नहीं", "not", "na", "ना"}

PROPERTY_TYPES: dict[str, tuple[str, ...]] = {
    "villa": ("villa", "विला", "bungalow", "बंगला", "independent house", "duplex"),
    "plot": ("plot", "प्लॉट", "प्लाट", "land", "zameen", "jameen", "ज़मीन", "जमीन"),
    "row_house": ("row house", "rowhouse", "row-house"),
    "penthouse": ("penthouse", "पेंटहाउस"),
    "studio": ("studio", "स्टूडियो"),
    "apartment": ("apartment", "flat", "फ्लैट", "फ़्लैट", "अपार्टमेंट", "flats"),
}
BHK_RE = re.compile(
    r"(\d+|one|two|three|four|five|ek|do|teen|char|chaar|एक|दो|तीन|चार|पांच|पाँच)"
    r"(?:\s*(?:or|/|-|ya|या|aur|and|,|से|se|to)\s*"
    r"(\d+|one|two|three|four|five|ek|do|teen|char|chaar|एक|दो|तीन|चार|पांच|पाँच))?"
    r"\s*-?\s*(?:bhk|b\.h\.k|बीएचके|bedroom|bed\s*room)",
    re.IGNORECASE,
)
SMALL = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ek": 1, "do": 2, "teen": 3,
    "char": 4, "chaar": 4, "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5,
}  # fmt: skip

READY = ("ready to move", "ready-to-move", "readytomove", "ready possession", "immediate",
         "immediately", "turant", "तुरंत", "रेडी टू मूव", "abhi shift", "ready flat",
         "ready hai", "ready chahiye")  # fmt: skip
MONTH_WORDS = {"month", "months", "mahine", "mahina", "mahino", "महीने", "महीना", "महीनों", "माह"}
YEAR_WORDS = {"year", "years", "saal", "sal", "साल", "वर्ष", "yr", "yrs"}
NEXT_YEAR = ("next year", "agle saal", "agle sal", "अगले साल")

LOAN_PRE_APPROVED = ("pre-approved", "pre approved", "preapproved", "loan approved",
                     "loan sanctioned", "sanctioned", "loan pass", "loan already",
                     "लोन approve", "लोन अप्रूव", "लोन पास", "loan ho gaya")  # fmt: skip
LOAN_NOT_NEEDED = ("no loan", "without loan", "loan nahi", "loan nahin", "loan ki zarurat nahi",
                   "full payment", "self funded", "self-funded", "cash payment", "pay cash",
                   "लोन नहीं", "लोन की ज़रूरत नहीं", "own funds", "don't need a loan",
                   "do not need a loan", "dont need a loan")  # fmt: skip
LOAN_NEEDED = ("loan chahiye", "need a loan", "need loan", "home loan", "bank loan",
               "loan lena", "loan lenge", "loan required", "लोन चाहिए", "लोन लेना", "होम लोन",
               "with loan", "via loan", "on loan", "emi")  # fmt: skip
INVESTMENT = ("investment", "invest", "rental income", "rent out", "निवेश", "इन्वेस्टमेंट",
              "resale value")  # fmt: skip
SELF_USE = ("self use", "self-use", "own use", "to live", "for living", "for my family",
            "for family", "rehne ke liye", "rehne", "khud ke liye", "khud rahenge",
            "रहने के लिए", "खुद के लिए", "end use")  # fmt: skip


@dataclass
class Extracted:
    requirements: Requirements
    evidence: dict[str, str] = field(default_factory=dict)


def _has(text: str, phrases: tuple[str, ...]) -> str | None:
    text = nfc(text)
    for phrase in phrases:
        phrase = nfc(phrase)
        if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", text):
            return phrase
    return None


def _money(quantities: list[Quantity]) -> list[Quantity]:
    return [q for q in quantities if q.kind == "money"]


def extract_budget(text: str) -> tuple[int | None, int | None]:
    # Speech-to-text sometimes writes a spoken lakh amount as dollars ("80 lakh" -> "$80,000"):
    # such digits cannot be trusted, so the agent confirms the amount in lakh instead.
    if "$" in text or "dollar" in text.lower():
        return None, None
    toks = tokens(text)
    found = scan(text)
    money = _money(found)
    budget_at = [i for i, t in enumerate(toks) if t in {nfc(w) for w in BUDGET_WORDS}]
    if not money and budget_at:
        # "budget 8000000" without a unit: only big plain numbers count.
        money = [q for q in found if q.kind == "plain" and q.value >= 100_000]
    if not money:
        return None, None
    # Group adjacent amounts into ranges: "72 to 78 lakh", "80 lakh se 1 crore".
    groups: list[list[Quantity]] = []
    for q in money:
        if groups and q.start - groups[-1][-1].end <= 1 and len(groups[-1]) == 1:
            connector = toks[groups[-1][-1].end] if groups[-1][-1].end < len(toks) else ""
            if connector in {"to", "se", "से", "-", "/", "and", "aur", "और", "till"} or (
                q.start == groups[-1][-1].end
            ):
                groups[-1].append(q)
                continue
        groups.append([q])
    if budget_at:
        groups.sort(key=lambda g: min(abs(g[0].start - b) for b in budget_at))
    chosen = groups[0]
    if len(chosen) == 2:
        lo, hi = sorted(int(q.value) for q in chosen)
        return lo, hi
    q = chosen[0]
    value = int(q.value)
    before = toks[max(0, q.start - 3) : q.start]
    after = toks[q.end : q.end + 3]
    window = " ".join(toks[max(0, q.start - 4) : q.end + 4])
    # "se zyada nahi" / "से ज़्यादा नहीं" = not more than: a lower marker negated is an upper limit.
    if (set(after) & LOWER_AFTER) and (set(toks[q.end : q.end + 4]) & NEGATIONS):
        return None, value
    if (set(before) & LOWER_BEFORE) or (set(after) & LOWER_AFTER) or _has(window, LOWER_PHRASES):
        return value, None
    if (set(before) & UPPER_BEFORE) or (set(after) & UPPER_AFTER) or _has(window, UPPER_PHRASES):
        return None, value
    return None, value


def _small(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    return SMALL.get(token.lower())


def extract_bhk(text: str) -> list[int]:
    out: list[int] = []
    for match in BHK_RE.finditer(text):
        a, b = _small(match.group(1)), _small(match.group(2)) if match.group(2) else None
        if a is not None and 0 < a <= 6:
            out.append(a)
        if b is not None and 0 < b <= 6:
            out.append(b)
    return sorted(set(out))


def extract_timeline(text: str) -> int | None:
    lowered = text.lower()
    if _has(lowered, READY):
        return 0
    if _has(lowered, NEXT_YEAR):
        return 12
    toks = tokens(lowered)
    for i, tok in enumerate(toks):
        if tok in MONTH_WORDS or tok in YEAR_WORDS:
            j = i - 1
            while j >= 0 and toks[j] in {"ke", "ki", "andar", "within", "in"}:
                j -= 1
            # Walk back to the start of the number words ("dedh saal", "6 mahine").
            k = j
            while k > 0 and read_number(toks, k - 1) is not None:
                k -= 1
            number = read_number(toks, k) if k >= 0 else None
            if number is None:
                continue
            months = number.value * (12 if tok in YEAR_WORDS else 1)
            if 0 < months <= 120:
                return int(months.to_integral_value())
    return None


def extract_loan(text: str) -> str | None:
    lowered = text.lower()
    if _has(lowered, LOAN_PRE_APPROVED):
        return "pre_approved"
    if _has(lowered, LOAN_NOT_NEEDED):
        return "not_needed"
    if _has(lowered, LOAN_NEEDED):
        return "needed"
    return None


def extract_purpose(text: str) -> str | None:
    lowered = text.lower()
    if _has(lowered, INVESTMENT):
        return "investment"
    if _has(lowered, SELF_USE):
        return "self_use"
    return None


def extract_property_type(text: str) -> str | None:
    lowered = text.lower()
    for ptype, words in PROPERTY_TYPES.items():
        if _has(lowered, words):
            return ptype
    return None


def location_aliases(localities: list[str]) -> list[str]:
    """Names a customer may use for a known place: "New Town Action Area II" is also "New
    Town" (its leading words); "Rajarhat" is itself. Longest first."""
    aliases: set[str] = set()
    for name in localities:
        words = name.split()
        for n in range(len(words), 0, -1):
            alias = " ".join(words[:n])
            if len(alias.replace(" ", "")) >= 5 and (n >= 2 or len(words) == 1):
                aliases.add(alias)
    return sorted(aliases, key=len, reverse=True)


def extract_locations(text: str, aliases: list[str]) -> list[str]:
    """Known place names in the text; "newtown" and "New  Town" match "New Town"."""
    lowered = text.lower()
    found: list[str] = []
    for alias in aliases:
        words = [re.escape(w) for w in alias.lower().split()]
        pattern = r"(?<![a-z])" + r"\s*".join(words) + r"(?![a-z])"
        if re.search(pattern, lowered) and not any(alias.lower() in f.lower() for f in found):
            found.append(alias)
    return found


def extract(text: str, *, localities: list[str] | None = None) -> Extracted:
    evidence: dict[str, str] = {}
    lo, hi = extract_budget(text)
    bhk = extract_bhk(text)
    ptype = extract_property_type(text)
    if ptype is None and bhk:
        ptype = "apartment"  # "2 BHK" alone means a flat; recorded as inferred
        evidence["property_type"] = "inferred from BHK"
    req = Requirements(
        budget_min_inr=lo,
        budget_max_inr=hi,
        bhk=bhk,
        property_type=ptype,
        timeline_months=extract_timeline(text),
        loan=extract_loan(text),
        purpose=extract_purpose(text),
        locations=extract_locations(text, location_aliases(localities or [])),
    )
    return Extracted(req, evidence)


def parse_budget_text(text: str) -> tuple[int | None, int | None]:
    """For ``save_requirements``: the LLM passes the customer's own words about the budget."""
    lo, hi = extract_budget(text)
    if lo is None and hi is None:
        # Bare "80 lakh"-style words without any context word still count here.
        money = [int(q.value) for q in scan(text) if q.kind == "money"]
        if len(money) >= 2:
            return min(money), max(money)
        if money:
            return None, money[0]
    return lo, hi
