"""Spoken and display formatting for facts, in English and Hindi.

The LLM never formats numbers or dates itself. Tools return the strings produced here, so the
voice engine says "85 lakh rupees" / "पचासी लाख रुपये" instead of "85,00,000".

Rules:

- Amounts are never rounded. English uses lakh/crore with up to two decimals only when that is
  exact (1.05 crore); otherwise it falls back to the full form (1 crore 23 lakh 45 thousand 678).
- Hindi uses natural spoken forms where they are exact: सवा (¼ more), डेढ़ (1½), ढाई (2½),
  साढ़े (N½), पौने (¼ less) for whole quarters of a lakh or crore; otherwise full number words.
- Every amount ends with the currency word ("rupees" / "रुपये"), ranges included. The phase plan's
  examples sometimes omit it ("1.2 to 1.5 crore"); one consistent rule is easier to hear and test.
- Display strings use the rupee sign and Indian digit grouping (₹85,00,000).
- Hindi number words are colloquial: 1250 is "बारह सौ पचास", as buyers say it.

Supported languages are registered in ``FORMATTERS``. Which languages are *used* comes from
``agent.languages`` in config; the Truth Layer refuses to start if one has no formatter here.

The Hindi wording should be reviewed by a native speaker before production use.
"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from typing import Protocol

LAKH = 100_000
CRORE = 10_000_000

# fmt: off
HINDI_0_99: tuple[str, ...] = (
    "शून्य", "एक", "दो", "तीन", "चार", "पाँच", "छह", "सात", "आठ", "नौ",
    "दस", "ग्यारह", "बारह", "तेरह", "चौदह", "पंद्रह", "सोलह", "सत्रह", "अठारह", "उन्नीस",
    "बीस", "इक्कीस", "बाईस", "तेईस", "चौबीस", "पच्चीस", "छब्बीस", "सत्ताईस", "अट्ठाईस", "उनतीस",
    "तीस", "इकतीस", "बत्तीस", "तैंतीस", "चौंतीस", "पैंतीस", "छत्तीस", "सैंतीस", "अड़तीस", "उनतालीस",
    "चालीस", "इकतालीस", "बयालीस", "तैंतालीस", "चवालीस", "पैंतालीस", "छियालीस", "सैंतालीस", "अड़तालीस",
    "उनचास",
    "पचास", "इक्यावन", "बावन", "तिरेपन", "चौवन", "पचपन", "छप्पन", "सत्तावन", "अट्ठावन", "उनसठ",
    "साठ", "इकसठ", "बासठ", "तिरेसठ", "चौंसठ", "पैंसठ", "छियासठ", "सड़सठ", "अड़सठ", "उनहत्तर",
    "सत्तर", "इकहत्तर", "बहत्तर", "तिहत्तर", "चौहत्तर", "पचहत्तर", "छिहत्तर", "सतहत्तर", "अठहत्तर",
    "उनासी",
    "अस्सी", "इक्यासी", "बयासी", "तिरासी", "चौरासी", "पचासी", "छियासी", "सत्तासी", "अट्ठासी", "नवासी",
    "नब्बे", "इक्यानवे", "बानवे", "तिरानवे", "चौरानवे", "पंचानवे", "छियानवे", "सत्तानवे", "अट्ठानवे",
    "निन्यानवे",
)
# fmt: on
assert len(HINDI_0_99) == 100

ENGLISH_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip
ENGLISH_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
HINDI_WEEKDAYS = ("सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार")

HINDI_MONTHS = (
    "जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून",
    "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर",
)  # fmt: skip


def _require_non_negative(n: int) -> None:
    if n < 0:
        raise ValueError(f"expected a non-negative number, got {n}")


# ------------------------------------------------------------------------------- display


def indian_grouping(n: int) -> str:
    """85,00,000 style grouping: last three digits, then groups of two."""
    sign = "-" if n < 0 else ""
    digits = str(abs(n))
    if len(digits) <= 3:
        return sign + digits
    head, tail = digits[:-3], digits[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return sign + ",".join([*groups, tail])


def _decimal_text(value: Decimal) -> str:
    """Plain decimal without trailing zeros: Decimal('1250.50') -> '1250.5'."""
    text = format(value.normalize(), "f")
    return text


def display_number(value: int | Decimal) -> str:
    if isinstance(value, int) or value == value.to_integral_value():
        return indian_grouping(int(value))
    whole = int(value)
    frac = _decimal_text(value - whole).removeprefix("0")
    return indian_grouping(whole) + frac


def display_inr(amount: int) -> str:
    _require_non_negative(amount)
    return f"₹{indian_grouping(amount)}"


def display_inr_range(low: int, high: int) -> str:
    return f"{display_inr(low)} – {display_inr(high)}"


def display_area(sqft: int | Decimal) -> str:
    return f"{display_number(sqft)} sq ft"


def display_month_year(d: date) -> str:
    return f"{ENGLISH_MONTHS[d.month - 1]} {d.year}"


def display_percent(p: Decimal | int) -> str:
    return f"{_decimal_text(Decimal(p))}%"


# ------------------------------------------------------------------------------- English


def _exact_units(amount: int, unit: int) -> str | None:
    """amount/unit with at most two decimals, only if exact (no rounding)."""
    whole, rem = divmod(amount, unit)
    if rem == 0:
        return str(whole)
    if (rem * 100) % unit == 0:
        return f"{whole}.{(rem * 100) // unit:02d}".rstrip("0")
    return None


def _english_compound(amount: int) -> str:
    crore, rem = divmod(amount, CRORE)
    lakh, rem = divmod(rem, LAKH)
    thousand, rest = divmod(rem, 1000)
    parts = []
    if crore:
        parts.append(f"{indian_grouping(crore)} crore")
    if lakh:
        parts.append(f"{lakh} lakh")
    if thousand:
        parts.append(f"{thousand} thousand")
    if rest or not parts:
        parts.append(str(rest))
    return " ".join(parts)


def _english_amount_parts(amount: int) -> tuple[str, str | None]:
    """(number, unit) for exact lakh/crore amounts, else (full text, None)."""
    if amount >= CRORE:
        exact = _exact_units(amount, CRORE)
        if exact is not None:
            return exact, "crore"
    elif amount >= LAKH:
        exact = _exact_units(amount, LAKH)
        if exact is not None:
            return exact, "lakh"
    else:
        return indian_grouping(amount), None
    return _english_compound(amount), None


def english_amount(amount: int) -> str:
    _require_non_negative(amount)
    number, unit = _english_amount_parts(amount)
    return f"{number} {unit}" if unit else number


class EnglishFormatter:
    code = "en"
    currency = "rupees"

    def inr(self, amount: int) -> str:
        return f"{english_amount(amount)} rupees"

    def inr_range(self, low: int, high: int) -> str:
        if low == high:
            return self.inr(low)
        _require_non_negative(low)
        _require_non_negative(high)
        (ln, lu), (hn, hu) = _english_amount_parts(low), _english_amount_parts(high)
        if lu is not None and lu == hu:
            return f"{ln} to {hn} {lu} rupees"
        return f"{english_amount(low)} to {english_amount(high)} rupees"

    def month_year(self, d: date) -> str:
        return f"{ENGLISH_MONTHS[d.month - 1]} {d.year}"

    def full_date(self, d: date) -> str:
        return f"{d.day} {ENGLISH_MONTHS[d.month - 1]} {d.year}"

    def area(self, sqft: int | Decimal) -> str:
        return f"{display_number(sqft)} square feet"

    def percent(self, p: Decimal | int) -> str:
        return f"{_decimal_text(Decimal(p))} percent"

    def count(self, n: int, singular: str, plural: str) -> str:
        return f"{n} {singular if n == 1 else plural}"

    def number(self, n: int) -> str:
        return str(n)

    def join(self, items: list[str]) -> str:
        if len(items) <= 1:
            return "".join(items)
        return ", ".join(items[:-1]) + f" and {items[-1]}"

    def weekday(self, d: date) -> str:
        return ENGLISH_WEEKDAYS[d.weekday()]

    def time_of_day(self, t: time) -> str:
        if t.hour == 12 and t.minute == 0:
            return "12 noon"
        suffix = "am" if t.hour < 12 else "pm"
        hour = t.hour % 12 or 12
        return f"{hour} {suffix}" if t.minute == 0 else f"{hour}:{t.minute:02d} {suffix}"

    def slot(self, d: date, t: time) -> str:
        return f"{self.weekday(d)} {d.day} {ENGLISH_MONTHS[d.month - 1]} at {self.time_of_day(t)}"


# --------------------------------------------------------------------------------- Hindi


def hindi_number(n: int) -> str:
    """Hindi number words (Indian system), colloquial hundreds for 1100–9999."""
    _require_non_negative(n)
    if n < 100:
        return HINDI_0_99[n]
    if 1100 <= n <= 9999 and (n // 100) % 10 != 0:
        hundreds, rest = divmod(n, 100)
        words = f"{HINDI_0_99[hundreds]} सौ"
        return f"{words} {HINDI_0_99[rest]}" if rest else words
    parts: list[str] = []
    crore, rem = divmod(n, CRORE)
    if crore:
        parts.append(f"{hindi_number(crore)} करोड़")
    lakh, rem = divmod(rem, LAKH)
    if lakh:
        parts.append(f"{HINDI_0_99[lakh]} लाख")
    thousand, rem = divmod(rem, 1000)
    if thousand:
        parts.append(f"{HINDI_0_99[thousand]} हज़ार")
    hundred, rem = divmod(rem, 100)
    if hundred:
        parts.append(f"{HINDI_0_99[hundred]} सौ")
    if rem:
        parts.append(HINDI_0_99[rem])
    return " ".join(parts)


def _hindi_quarters(quarters: int, unit: str) -> str | None:
    """Spoken form for a whole number of quarter-units, or None if no natural form."""
    whole, frac = divmod(quarters, 4)
    if frac == 0:
        return f"{hindi_number(whole)} {unit}"
    if frac == 1 and whole >= 1:
        return f"सवा {unit}" if whole == 1 else f"सवा {hindi_number(whole)} {unit}"
    if frac == 2 and whole >= 1:
        if whole == 1:
            return f"डेढ़ {unit}"
        if whole == 2:
            return f"ढाई {unit}"
        return f"साढ़े {hindi_number(whole)} {unit}"
    if frac == 3 and whole >= 1:
        return f"पौने {hindi_number(whole + 1)} {unit}"
    return None


def hindi_amount(amount: int) -> str:
    _require_non_negative(amount)
    natural: str | None = None
    if amount >= CRORE and amount % (CRORE // 4) == 0:
        natural = _hindi_quarters(amount // (CRORE // 4), "करोड़")
    elif LAKH <= amount < CRORE and amount % (LAKH // 4) == 0:
        natural = _hindi_quarters(amount // (LAKH // 4), "लाख")
    return natural or hindi_number(amount)


def hindi_decimal(value: Decimal | int) -> str:
    """Hindi words for small decimals: 2.5 -> ढाई, 12.5 -> साढ़े बारह, 0.5 -> आधा."""
    value = Decimal(value)
    if value < 0:
        raise ValueError(f"expected a non-negative number, got {value}")
    whole = int(value)
    frac = value - whole
    if frac == 0:
        return hindi_number(whole)
    if frac == Decimal("0.5") and whole == 0:
        return "आधा"
    # Quarter words only for small numbers: "साढ़े नौ सौ अस्सी" (980.5) is ambiguous aloud.
    if whole < 100 and frac in (Decimal("0.25"), Decimal("0.5"), Decimal("0.75")):
        natural = _hindi_quarters(int(value * 4), "")
        if natural:
            return natural.strip()
    digits = _decimal_text(frac).removeprefix("0.")
    return f"{hindi_number(whole)} दशमलव " + " ".join(HINDI_0_99[int(d)] for d in digits)


class HindiFormatter:
    code = "hi"
    currency = "रुपये"

    def inr(self, amount: int) -> str:
        return f"{hindi_amount(amount)} रुपये"

    def inr_range(self, low: int, high: int) -> str:
        if low == high:
            return self.inr(low)
        return f"{hindi_amount(low)} से {hindi_amount(high)} रुपये"

    def month_year(self, d: date) -> str:
        return f"{HINDI_MONTHS[d.month - 1]} {d.year}"

    def full_date(self, d: date) -> str:
        return f"{d.day} {HINDI_MONTHS[d.month - 1]} {d.year}"

    def area(self, sqft: int | Decimal) -> str:
        value = Decimal(sqft)
        words = hindi_number(int(value)) if value == int(value) else hindi_decimal(value)
        return f"{words} स्क्वायर फ़ीट"

    def percent(self, p: Decimal | int) -> str:
        return f"{hindi_decimal(p)} प्रतिशत"

    def count(self, n: int, singular: str, plural: str) -> str:
        return f"{hindi_number(n)} {singular if n == 1 else plural}"

    def number(self, n: int) -> str:
        return hindi_number(n)

    def join(self, items: list[str]) -> str:
        if len(items) <= 1:
            return "".join(items)
        return ", ".join(items[:-1]) + f" और {items[-1]}"

    def weekday(self, d: date) -> str:
        return HINDI_WEEKDAYS[d.weekday()]

    def time_of_day(self, t: time) -> str:
        """सुबह ग्यारह बजे, दोपहर दो बजे, शाम साढ़े चार बजे, रात आठ बजे."""
        h = t.hour
        if 4 <= h < 12:
            period = "सुबह"
        elif 12 <= h < 16:
            period = "दोपहर"
        elif 16 <= h < 19:
            period = "शाम"
        else:
            period = "रात"
        h12 = h % 12 or 12
        nxt = h12 % 12 + 1
        if t.minute == 0:
            spoken = f"{hindi_number(h12)} बजे"
        elif t.minute == 30:
            spoken = {1: "डेढ़ बजे", 2: "ढाई बजे"}.get(h12, f"साढ़े {hindi_number(h12)} बजे")
        elif t.minute == 15:
            spoken = f"सवा {hindi_number(h12)} बजे"
        elif t.minute == 45:
            spoken = f"पौने {hindi_number(nxt)} बजे"
        else:
            spoken = f"{hindi_number(h12)} बजकर {hindi_number(t.minute)} मिनट"
        return f"{period} {spoken}"

    def slot(self, d: date, t: time) -> str:
        return f"{self.weekday(d)} {d.day} {HINDI_MONTHS[d.month - 1]}, {self.time_of_day(t)}"


class SpokenFormatter(Protocol):
    code: str
    currency: str

    def inr(self, amount: int) -> str: ...
    def inr_range(self, low: int, high: int) -> str: ...
    def month_year(self, d: date) -> str: ...
    def full_date(self, d: date) -> str: ...
    def area(self, sqft: int | Decimal) -> str: ...
    def percent(self, p: Decimal | int) -> str: ...
    def count(self, n: int, singular: str, plural: str) -> str: ...
    def number(self, n: int) -> str: ...
    def join(self, items: list[str]) -> str: ...
    def weekday(self, d: date) -> str: ...
    def time_of_day(self, t: time) -> str: ...
    def slot(self, d: date, t: time) -> str: ...


# Languages this template can speak. Config chooses which of these a client uses.
FORMATTERS: dict[str, SpokenFormatter] = {"en": EnglishFormatter(), "hi": HindiFormatter()}


def formatter_for(language: str) -> SpokenFormatter:
    try:
        return FORMATTERS[language]
    except KeyError:
        raise ValueError(f"no spoken formatter for language {language!r}") from None
