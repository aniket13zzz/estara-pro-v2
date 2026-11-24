"""Truth Layer formatters: English and Hindi spoken strings, Indian grouping, dates, areas."""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal

import pytest

from app.truth.formatters import (
    FORMATTERS,
    HINDI_0_99,
    EnglishFormatter,
    HindiFormatter,
    display_area,
    display_inr,
    display_inr_range,
    display_percent,
    formatter_for,
    hindi_decimal,
    hindi_number,
    indian_grouping,
)

EN = EnglishFormatter()
HI = HindiFormatter()

# ------------------------------------------------------------------ Indian digit grouping


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (0, "0"),
        (999, "999"),
        (1000, "1,000"),
        (99_999, "99,999"),
        (1_00_000, "1,00,000"),
        (85_00_000, "85,00,000"),
        (1_20_00_000, "1,20,00,000"),
        (12_75_00_000, "12,75,00,000"),
        (1_23_45_67_890, "1,23,45,67,890"),
    ],
)
def test_indian_grouping(n: int, expected: str) -> None:
    assert indian_grouping(n) == expected


def test_display_strings() -> None:
    assert display_inr(8_500_000) == "₹85,00,000"
    assert display_inr_range(12_000_000, 15_000_000) == "₹1,20,00,000 – ₹1,50,00,000"
    assert display_area(1250) == "1,250 sq ft"
    assert display_area(Decimal("1250.50")) == "1,250.5 sq ft"
    assert display_percent(Decimal("2.50")) == "2.5%"


# ------------------------------------------------------------------------ English amounts


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (100_000, "1 lakh rupees"),  # 1 lakh
        (8_500_000, "85 lakh rupees"),  # plan example
        (9_900_000, "99 lakh rupees"),  # 99 lakh
        (10_000_000, "1 crore rupees"),  # 1 crore
        (10_500_000, "1.05 crore rupees"),  # 1.05 crore
        (12_000_000, "1.2 crore rupees"),
        (15_000_000, "1.5 crore rupees"),  # 1.5 crore
        (127_500_000, "12.75 crore rupees"),  # 12.75 crore
        (150_000, "1.5 lakh rupees"),
        (8_550_000, "85.5 lakh rupees"),
        (75_000, "75,000 rupees"),
        (50_000, "50,000 rupees"),
        # Not exact at two decimals: never rounded, spoken in full.
        (8_537_500, "85 lakh 37 thousand 500 rupees"),
        (12_345_678, "1 crore 23 lakh 45 thousand 678 rupees"),
        (10_000_001, "1 crore 1 rupees"),
        (2_500_000_000, "250 crore rupees"),
    ],
)
def test_english_inr(amount: int, expected: str) -> None:
    assert EN.inr(amount) == expected


@pytest.mark.parametrize(
    ("low", "high", "expected"),
    [
        (12_000_000, 15_000_000, "1.2 to 1.5 crore rupees"),  # plan example
        (7_500_000, 9_000_000, "75 to 90 lakh rupees"),
        (8_500_000, 12_000_000, "85 lakh to 1.2 crore rupees"),
        (8_500_000, 8_500_000, "85 lakh rupees"),
        (8_537_500, 9_000_000, "85 lakh 37 thousand 500 to 90 lakh rupees"),
    ],
)
def test_english_inr_range(low: int, high: int, expected: str) -> None:
    assert EN.inr_range(low, high) == expected


# -------------------------------------------------------------------------- Hindi amounts


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (100_000, "एक लाख रुपये"),  # 1 lakh
        (8_500_000, "पचासी लाख रुपये"),  # plan example
        (9_900_000, "निन्यानवे लाख रुपये"),  # 99 lakh
        (10_000_000, "एक करोड़ रुपये"),  # 1 crore
        (10_500_000, "एक करोड़ पाँच लाख रुपये"),  # 1.05 crore
        (12_000_000, "एक करोड़ बीस लाख रुपये"),  # 1.2 crore
        (12_500_000, "सवा करोड़ रुपये"),  # 1.25 crore
        (15_000_000, "डेढ़ करोड़ रुपये"),  # 1.5 crore
        (17_500_000, "पौने दो करोड़ रुपये"),  # 1.75 crore
        (25_000_000, "ढाई करोड़ रुपये"),
        (35_000_000, "साढ़े तीन करोड़ रुपये"),
        (22_500_000, "सवा दो करोड़ रुपये"),
        (127_500_000, "पौने तेरह करोड़ रुपये"),  # 12.75 crore
        (150_000, "डेढ़ लाख रुपये"),
        (250_000, "ढाई लाख रुपये"),
        (8_550_000, "साढ़े पचासी लाख रुपये"),
        (7_200_000, "बहत्तर लाख रुपये"),
        (75_000, "पचहत्तर हज़ार रुपये"),
        (8_537_500, "पचासी लाख सैंतीस हज़ार पाँच सौ रुपये"),
        (12_345_678, "एक करोड़ तेईस लाख पैंतालीस हज़ार छह सौ अठहत्तर रुपये"),
        (2_500_000_000, "दो सौ पचास करोड़ रुपये"),
    ],
)
def test_hindi_inr(amount: int, expected: str) -> None:
    assert HI.inr(amount) == expected


@pytest.mark.parametrize(
    ("low", "high", "expected"),
    [
        (12_000_000, 15_000_000, "एक करोड़ बीस लाख से डेढ़ करोड़ रुपये"),  # plan example
        (7_500_000, 9_000_000, "पचहत्तर लाख से नब्बे लाख रुपये"),
        (8_500_000, 8_500_000, "पचासी लाख रुपये"),
    ],
)
def test_hindi_inr_range(low: int, high: int, expected: str) -> None:
    assert HI.inr_range(low, high) == expected


# ---------------------------------------------------------------------- dates and areas


@pytest.mark.parametrize(
    ("d", "en", "hi"),
    [
        (date(2027, 12, 31), "December 2027", "दिसंबर 2027"),  # plan example
        (date(2026, 1, 15), "January 2026", "जनवरी 2026"),
        (date(2028, 2, 1), "February 2028", "फ़रवरी 2028"),
        (date(2026, 9, 30), "September 2026", "सितंबर 2026"),
    ],
)
def test_month_year(d: date, en: str, hi: str) -> None:
    assert EN.month_year(d) == en
    assert HI.month_year(d) == hi


def test_full_date() -> None:
    assert EN.full_date(date(2026, 10, 17)) == "17 October 2026"
    assert HI.full_date(date(2026, 10, 17)) == "17 अक्टूबर 2026"


@pytest.mark.parametrize(
    ("sqft", "en", "hi"),
    [
        (1250, "1,250 square feet", "बारह सौ पचास स्क्वायर फ़ीट"),  # plan example
        (650, "650 square feet", "छह सौ पचास स्क्वायर फ़ीट"),
        (1000, "1,000 square feet", "एक हज़ार स्क्वायर फ़ीट"),
        (2050, "2,050 square feet", "दो हज़ार पचास स्क्वायर फ़ीट"),
        (Decimal("1250.00"), "1,250 square feet", "बारह सौ पचास स्क्वायर फ़ीट"),
        (Decimal("980.5"), "980.5 square feet", "नौ सौ अस्सी दशमलव पाँच स्क्वायर फ़ीट"),
    ],
)
def test_area(sqft: int | Decimal, en: str, hi: str) -> None:
    assert EN.area(sqft) == en
    assert HI.area(sqft) == hi


# ------------------------------------------------------------ numbers, percents, counts


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (0, "शून्य"),
        (21, "इक्कीस"),
        (85, "पचासी"),
        (100, "एक सौ"),
        (105, "एक सौ पाँच"),
        (1100, "ग्यारह सौ"),
        (3400, "चौंतीस सौ"),
        (10_000, "दस हज़ार"),
        (1_01_250, "एक लाख एक हज़ार दो सौ पचास"),
    ],
)
def test_hindi_number(n: int, expected: str) -> None:
    assert hindi_number(n) == expected


@pytest.mark.parametrize(
    ("p", "en", "hi"),
    [
        (10, "10 percent", "दस प्रतिशत"),
        (Decimal("2.5"), "2.5 percent", "ढाई प्रतिशत"),
        (Decimal("12.5"), "12.5 percent", "साढ़े बारह प्रतिशत"),
        (Decimal("1.5"), "1.5 percent", "डेढ़ प्रतिशत"),
        (Decimal("0.5"), "0.5 percent", "आधा प्रतिशत"),
        (Decimal("7.3"), "7.3 percent", "सात दशमलव तीन प्रतिशत"),
    ],
)
def test_percent(p: Decimal | int, en: str, hi: str) -> None:
    assert EN.percent(p) == en
    assert HI.percent(p) == hi


def test_counts() -> None:
    assert EN.count(1, "unit", "units") == "1 unit"
    assert EN.count(3, "unit", "units") == "3 units"
    assert HI.count(3, "unit", "units") == "तीन units"


def test_join_lists() -> None:
    assert EN.join([]) == ""
    assert EN.join(["Gym"]) == "Gym"
    assert EN.join(["Swimming pool", "Gym", "Clubhouse"]) == "Swimming pool, Gym and Clubhouse"
    assert HI.join(["Swimming pool", "Gym", "Clubhouse"]) == "Swimming pool, Gym और Clubhouse"


@pytest.mark.parametrize(
    ("t", "en", "hi"),
    [
        (time(11, 0), "11 am", "सुबह ग्यारह बजे"),
        (time(14, 0), "2 pm", "दोपहर दो बजे"),
        (time(16, 0), "4 pm", "शाम चार बजे"),
        (time(12, 0), "12 noon", "दोपहर बारह बजे"),
        (time(19, 30), "7:30 pm", "रात साढ़े सात बजे"),
        (time(13, 30), "1:30 pm", "दोपहर डेढ़ बजे"),
        (time(14, 30), "2:30 pm", "दोपहर ढाई बजे"),
        (time(10, 15), "10:15 am", "सुबह सवा दस बजे"),
        (time(10, 45), "10:45 am", "सुबह पौने ग्यारह बजे"),
        (time(9, 20), "9:20 am", "सुबह नौ बजकर बीस मिनट"),
    ],
)
def test_time_of_day(t: time, en: str, hi: str) -> None:
    assert EN.time_of_day(t) == en
    assert HI.time_of_day(t) == hi


def test_visit_slot() -> None:
    d = date(2026, 10, 17)  # a Saturday
    assert EN.slot(d, time(11, 0)) == "Saturday 17 October at 11 am"
    assert HI.slot(d, time(11, 0)) == "शनिवार 17 अक्टूबर, सुबह ग्यारह बजे"
    assert HI.weekday(date(2026, 10, 18)) == "रविवार"


def test_hindi_decimal_quarters() -> None:
    assert hindi_decimal(Decimal("3.75")) == "पौने चार"
    assert hindi_decimal(Decimal("2.25")) == "सवा दो"


# ------------------------------------------------------------------------------- safety


def test_hindi_word_table_is_complete_and_devanagari() -> None:
    assert len(set(HINDI_0_99)) == 100
    assert all(any("ऀ" <= ch <= "ॿ" for ch in w) for w in HINDI_0_99)


def test_negative_amounts_rejected() -> None:
    with pytest.raises(ValueError):
        EN.inr(-1)
    with pytest.raises(ValueError):
        HI.inr(-1)


def test_formatter_registry() -> None:
    assert set(FORMATTERS) >= {"en", "hi"}
    assert formatter_for("hi").inr(8_500_000) == "पचासी लाख रुपये"
    with pytest.raises(ValueError, match="no spoken formatter"):
        formatter_for("ta")


@pytest.mark.parametrize("amount", [100_000, 4_500_000, 8_537_500, 12_345_678, 127_500_000])
def test_spoken_strings_never_contain_raw_grouped_digits(amount: int) -> None:
    # The voice engine must never be handed "85,00,000"; Hindi is words only.
    assert "," not in EN.inr(amount)
    assert not any(ch.isdigit() for ch in HI.inr(amount))
