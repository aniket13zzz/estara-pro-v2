"""One deterministic reader for quantities in English, Hindi and Hinglish text.

Used twice, so both sides agree on what a number is:

- requirement extraction reads the customer's budget ("80 lakh", "1 crore tak",
  "ek se dedh crore", "अस्सी लाख", "₹85,00,000");
- the grounding guard reads the agent's reply and every tool result, and refuses any amount,
  date, time, area, percentage or count in the reply that no tool (or the customer) supplied.

Numbers can be digits ("1.2", "85,00,000", "85L", "1.2cr"), English words ("seventy two"),
Romanised Hindi ("pachattar", "dedh", "sawa do") or Devanagari ("पचहत्तर", "डेढ़",
"एक करोड़ बीस लाख", "बारह सौ पचास"). Number *words* only count when a unit follows them
(lakh, crore, rupees, square feet, percent, BHK, units, बजे, am/pm), so "एक सेकंड" or "do
options" are not quantities. Digits always count.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.truth.formatters import HINDI_0_99, HINDI_MONTHS

Kind = Literal["money", "area", "percent", "time", "count", "bhk", "plain"]

DEVANAGARI_RANGE = "ऀ-ॿ"
TOKEN = re.compile(
    "₹|%|\\d+:\\d{2}|\\d[\\d,]*(?:\\.\\d+)?[a-z]*|[a-z]+|[" + DEVANAGARI_RANGE + "]+|[-/]",
    re.IGNORECASE,
)

ENGLISH_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}  # fmt: skip
ROMAN_HINDI = {
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6,
    "chhah": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10, "gyarah": 11, "barah": 12,
    "baarah": 12, "terah": 13, "chaudah": 14, "pandrah": 15, "solah": 16, "satrah": 17,
    "atharah": 18, "unnis": 19, "bees": 20, "pachees": 25, "pachchees": 25, "tees": 30,
    "chalis": 40, "chaalis": 40, "chalees": 40, "pachas": 50, "pachaas": 50, "saath": 60,
    "sattar": 70, "pachattar": 75, "pachhattar": 75, "assi": 80, "assee": 80, "pachasi": 85,
    "pachaasi": 85, "nabbe": 90, "nabbey": 90,
}  # fmt: skip
DEVANAGARI = {word: n for n, word in enumerate(HINDI_0_99)}
DEVANAGARI.update({"पांच": 5, "छः": 6, "छ": 6})
WHOLE_FRACTIONS = {
    "dedh": Decimal("1.5"), "डेढ़": Decimal("1.5"), "dhai": Decimal("2.5"),
    "dhaai": Decimal("2.5"), "ढाई": Decimal("2.5"),
}  # fmt: skip
PREFIXES = {
    "sawa": Decimal("0.25"), "सवा": Decimal("0.25"), "saadhe": Decimal("0.5"),
    "sadhe": Decimal("0.5"), "साढ़े": Decimal("0.5"), "paune": Decimal("-0.25"),
    "पौने": Decimal("-0.25"),
}  # fmt: skip
HUNDRED = {"hundred", "sau", "सौ"}
THOUSAND = {"हज़ार", "हजार", "thousand", "hazar", "hazaar"}
LAKH = {"लाख", "lakh", "lakhs", "lac", "lacs"}
CRORE = {"करोड़", "करोड", "crore", "crores"}

MONEY_UNITS = {
    **dict.fromkeys([*LAKH, "l", "lk"], Decimal(100_000)),
    **dict.fromkeys([*CRORE, "cr", "crs"], Decimal(10**7)),
    **dict.fromkeys([*THOUSAND, "k"], Decimal(1000)),
}
RUPEE_WORDS = {"rupees", "rupee", "rs", "inr", "रुपये", "रुपए", "रूपये", "रुपया"}
AREA_WORDS = {"sq", "sqft", "square", "स्क्वायर", "वर्ग"}
FEET_WORDS = {"ft", "feet", "foot", "फ़ीट", "फीट", "फुट"}
PERCENT_WORDS = {"percent", "%", "प्रतिशत", "pratishat", "फ़ीसदी"}
TIME_WORDS = {"am", "pm", "baje", "बजे", "noon"}
COUNT_WORDS = {"unit", "units", "flat", "flats", "apartments", "homes", "options", "slots"}
BHK_WORDS = {"bhk", "बीएचके", "bedroom", "bedrooms"}
RANGE_CONNECTORS = {"to", "se", "से", "-", "/", "and", "aur", "और", "till"}
HINDI_AFTERNOON = {"दोपहर", "शाम", "रात", "dopahar", "shaam", "raat"}

ENGLISH_MONTHS = (
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december",
)  # fmt: skip
MONTH_INDEX = {m: i + 1 for i, m in enumerate(ENGLISH_MONTHS)}
MONTH_INDEX.update({m[:3]: i + 1 for i, m in enumerate(ENGLISH_MONTHS)})
MONTH_INDEX.update({m: i + 1 for i, m in enumerate(HINDI_MONTHS)})
MONTH_INDEX.update({"फरवरी": 2, "सितम्बर": 9, "अक्तूबर": 10, "नवम्बर": 11, "दिसम्बर": 12})


def nfc(text: str) -> str:
    """Devanagari nukta letters (ड़, ज़, फ़) have two encodings; NFC makes them one."""
    return unicodedata.normalize("NFC", text)


def _nfc_keys[V](table: dict[str, V]) -> dict[str, V]:
    return {nfc(k): v for k, v in table.items()}


DEVANAGARI = _nfc_keys(DEVANAGARI)
WHOLE_FRACTIONS = _nfc_keys(WHOLE_FRACTIONS)
PREFIXES = _nfc_keys(PREFIXES)
MONEY_UNITS = _nfc_keys(MONEY_UNITS)
MONTH_INDEX = _nfc_keys(MONTH_INDEX)
THOUSAND, LAKH, CRORE = ({nfc(w) for w in ws} for ws in (THOUSAND, LAKH, CRORE))
RUPEE_WORDS, AREA_WORDS, FEET_WORDS, PERCENT_WORDS, TIME_WORDS, HINDI_AFTERNOON = (
    {nfc(w) for w in ws}
    for ws in (RUPEE_WORDS, AREA_WORDS, FEET_WORDS, PERCENT_WORDS, TIME_WORDS, HINDI_AFTERNOON)
)
RANGE_CONNECTORS = {nfc(w) for w in RANGE_CONNECTORS}


@dataclass(frozen=True)
class Quantity:
    value: Decimal  # money in rupees, area in sq ft, time in hours (13.5 = 1:30 pm)
    kind: Kind
    start: int  # token indexes, for nearby-word checks
    end: int
    from_words: bool = False


@dataclass(frozen=True)
class NumberRead:
    """A number read from tokens. ``base`` holds completed higher magnitudes ("एक करोड़" in
    "एक करोड़ बीस लाख"); a unit word after the number multiplies only ``tail``."""

    base: Decimal
    tail: Decimal
    end: int
    words: bool

    @property
    def value(self) -> Decimal:
        return self.base + self.tail

    def times(self, mult: Decimal) -> Decimal:
        return self.base + self.tail * mult


def tokens(text: str) -> list[str]:
    out: list[str] = []
    for tok in TOKEN.findall(nfc(text).lower()):
        glued = re.fullmatch(r"(\d[\d,]*(?:\.\d+)?)([a-z]+)", tok)
        if glued:
            out += [glued.group(1), glued.group(2)]  # 85l, 1.2cr, 80lakh, 2bhk
        else:
            out.append(tok)
    return out


def _word_value(tok: str) -> int | None:
    if tok in ENGLISH_UNITS:
        return ENGLISH_UNITS[tok]
    if tok in ROMAN_HINDI:
        return ROMAN_HINDI[tok]
    return DEVANAGARI.get(tok)


def _digits(tok: str) -> Decimal | None:
    if re.fullmatch(r"\d[\d,]*(?:\.\d+)?", tok):
        return Decimal(tok.replace(",", ""))
    return None


def _continues(toks: list[str], j: int) -> bool:
    """A magnitude word is inside a number only if more number words follow it
    ("एक करोड़ बीस लाख"); otherwise it is the unit and the caller reads it."""
    return j < len(toks) and (
        _word_value(toks[j]) is not None or toks[j] in PREFIXES or toks[j] in WHOLE_FRACTIONS
    )


def read_number(toks: list[str], i: int) -> NumberRead | None:
    """The number starting at token ``i``, using Indian number-word grammar."""
    if i >= len(toks):
        return None
    d = _digits(toks[i])
    if d is not None:
        j = i + 1
        if j + 1 < len(toks) and toks[j] == "point" and _digits(toks[j + 1]) is not None:
            d = Decimal(f"{toks[i]}.{toks[j + 1]}")
            j += 2
        return NumberRead(Decimal(0), d, j, False)
    prefix = PREFIXES.get(toks[i])
    if prefix is not None:
        inner = read_number(toks, i + 1)
        if inner is not None and inner.words:
            return NumberRead(inner.base, inner.tail + prefix, inner.end, True)
        return NumberRead(Decimal(0), Decimal(1) + prefix, i + 1, True)  # "सवा करोड़"
    if toks[i] in WHOLE_FRACTIONS:
        return NumberRead(Decimal(0), WHOLE_FRACTIONS[toks[i]], i + 1, True)
    total = Decimal(0)
    current = Decimal(0)
    j = i
    seen = False
    while j < len(toks):
        tok = toks[j]
        value = _word_value(tok)
        if value is not None:
            current += value
            seen = True
        elif tok in HUNDRED and seen:
            current = (current or Decimal(1)) * 100
        elif tok in THOUSAND and seen and _continues(toks, j + 1):
            total += (current or Decimal(1)) * 1000
            current = Decimal(0)
        elif tok in LAKH and seen and _continues(toks, j + 1):
            total += current * 100_000
            current = Decimal(0)
        elif tok in CRORE and seen and _continues(toks, j + 1):
            total = (total + current) * Decimal(10**7)
            current = Decimal(0)
        else:
            break
        j += 1
    if not seen:
        return None
    return NumberRead(total, current, j, True)


def _unit(toks: list[str], j: int) -> tuple[Kind, Decimal, int] | None:
    """Unit right after a number: (kind, multiplier or pm offset, next index)."""
    if j >= len(toks):
        return None
    tok = toks[j]
    if tok in MONEY_UNITS:
        k = j + 1
        if k < len(toks) and toks[k] in RUPEE_WORDS:
            k += 1
        return "money", MONEY_UNITS[tok], k
    if tok in RUPEE_WORDS:
        return "money", Decimal(1), j + 1
    if tok in AREA_WORDS:
        k = j + 1
        while k < len(toks) and toks[k] in FEET_WORDS:
            k += 1
        return "area", Decimal(1), k
    if tok in PERCENT_WORDS:
        return "percent", Decimal(1), j + 1
    if tok in TIME_WORDS:
        return "time", Decimal(12) if tok == "pm" else Decimal(0), j + 1
    if tok in COUNT_WORDS:
        return "count", Decimal(1), j + 1
    if tok in BHK_WORDS:
        return "bhk", Decimal(1), j + 1
    return None


def _clock_hour(toks: list[str], i: int, j: int, hour: Decimal, offset: Decimal) -> Decimal:
    word = toks[j]
    if word == "noon":
        return Decimal(12)
    if word in {"am", "pm"}:
        return hour % 12 + offset
    before = toks[i - 1] if i > 0 else ""
    return hour + 12 if before in HINDI_AFTERNOON and hour < 12 else hour


def scan(text: str) -> list[Quantity]:
    toks = tokens(text)
    found: list[Quantity] = []
    i = 0
    while i < len(toks):
        if re.fullmatch(r"\d{1,2}:\d{2}", toks[i]):
            h, m = toks[i].split(":")
            value = Decimal(h) + Decimal(m) / 60
            if i + 1 < len(toks) and toks[i + 1] == "pm" and value < 12:
                value += 12
            found.append(Quantity(value, "time", i, i + 1))
            i += 1
            continue
        rupee = toks[i] == "₹"
        number = read_number(toks, i + 1 if rupee else i)
        if number is None:
            i += 1
            continue
        j = number.end
        unit = _unit(toks, j)
        if unit is None and j < len(toks) and toks[j] in RANGE_CONNECTORS:
            # "72 to 78 lakh", "ek se dedh crore": the first number takes the second's unit.
            second = read_number(toks, j + 1)
            if second is not None:
                shared = _unit(toks, second.end)
                if shared is not None and shared[0] in {"money", "area", "percent"}:
                    unit = (shared[0], shared[1], j)
        if unit is None:
            if rupee:
                found.append(Quantity(number.value, "money", i, j, number.words))
            elif not number.words:
                found.append(Quantity(number.value, "plain", i, j))
            i = max(j, i + 1)
            continue
        kind, mult, k = unit
        if kind == "time":
            value = _clock_hour(toks, i, j, number.value, mult)
        elif kind == "money":
            value = number.times(mult)
        else:
            value = number.value
        found.append(Quantity(value, kind, i, k, number.words))
        i = max(k, j, i + 1)
    return found


def scan_dates(text: str) -> set[str]:
    """``YYYY-MM`` for a month with a year, ``MM-DD`` for a day with a month."""
    toks = tokens(text)
    out: set[str] = set()
    for i, tok in enumerate(toks):
        month = MONTH_INDEX.get(tok)
        if month is None:
            continue
        if i + 1 < len(toks) and re.fullmatch(r"(19|20)\d\d", toks[i + 1]):
            out.add(f"{toks[i + 1]}-{month:02d}")
        if i > 0 and re.fullmatch(r"\d{1,2}", toks[i - 1]):
            out.add(f"{month:02d}-{int(toks[i - 1]):02d}")
    return out
