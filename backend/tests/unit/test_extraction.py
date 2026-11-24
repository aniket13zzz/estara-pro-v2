"""Deterministic requirement extraction: 30+ phrasings across English, Hindi and Hinglish."""

from __future__ import annotations

from typing import Any

import pytest

from app.brain.extraction import (
    extract,
    extract_budget,
    location_aliases,
    parse_budget_text,
)

LOCALITIES = ["New Town Action Area II", "Rajarhat", "Behala", "Kolkata"]
LAKH = 100_000
CRORE = 10_000_000


# ------------------------------------------------------------------------------- budgets

BUDGETS: list[tuple[str, tuple[int | None, int | None]]] = [
    # English
    ("My budget is 80 lakh", (None, 80 * LAKH)),
    ("around 85 lakhs", (None, 85 * LAKH)),
    ("under 1 crore please", (None, CRORE)),
    ("budget 1.2 cr", (None, 12 * CRORE // 10)),
    ("between 80 lakh and 1 crore", (80 * LAKH, CRORE)),
    ("1.2 to 1.5 crore", (12 * CRORE // 10, 15 * CRORE // 10)),
    ("70-90 lakh", (70 * LAKH, 90 * LAKH)),
    ("at least 60 lakh", (60 * LAKH, None)),
    ("above 1 crore", (CRORE, None)),
    ("up to 75 lakh", (None, 75 * LAKH)),
    ("₹85,00,000 max", (None, 85 * LAKH)),
    ("seventy two lakh rupees", (None, 72 * LAKH)),
    ("80L", (None, 80 * LAKH)),
    ("my budget is 8000000", (None, 80 * LAKH)),
    # Hinglish
    ("1 crore tak", (None, CRORE)),
    ("budget ek se dedh crore hai", (CRORE, 15 * CRORE // 10)),
    ("assi lakh tak chalega", (None, 80 * LAKH)),
    ("sawa crore se zyada nahi", (None, 125 * CRORE // 100)),
    ("dhai crore", (None, 25 * CRORE // 10)),
    ("90 lakh se kam", (None, 90 * LAKH)),
    ("50 lakh se zyada", (50 * LAKH, None)),
    # Hindi
    ("बजट अस्सी लाख तक है", (None, 80 * LAKH)),
    ("डेढ़ करोड़", (None, 15 * CRORE // 10)),
    ("एक करोड़ बीस लाख से डेढ़ करोड़", (12 * CRORE // 10, 15 * CRORE // 10)),
    ("पचहत्तर लाख रुपये", (None, 75 * LAKH)),
    ("सवा करोड़ तक", (None, 125 * CRORE // 100)),
    ("साठ लाख से ऊपर", (60 * LAKH, None)),
]


@pytest.mark.parametrize(("text", "expected"), BUDGETS)
def test_budget(text: str, expected: tuple[int | None, int | None]) -> None:
    assert extract_budget(text) == expected


@pytest.mark.parametrize("text", ["I want a 2 BHK", "12th floor please", "3 bedrooms", "दो BHK"])
def test_numbers_that_are_not_budgets(text: str) -> None:
    assert extract_budget(text) == (None, None)


def test_budget_word_picks_the_right_amount() -> None:
    text = "I saw one for 1.5 crore but my budget is 90 lakh"
    assert extract_budget(text) == (None, 90 * LAKH)


def test_budget_text_from_the_llm() -> None:
    assert parse_budget_text("ek se dedh crore") == (CRORE, 15 * CRORE // 10)
    assert parse_budget_text("80 lakh") == (None, 80 * LAKH)
    assert parse_budget_text("not sure yet") == (None, None)


# ------------------------------------------------------------------------- whole messages

MESSAGES: list[tuple[str, dict[str, Any]]] = [
    (
        "I'm looking for a 2 BHK in New Town, around 80 lakh",
        {"bhk": [2], "locations": ["New Town"], "budget_max_inr": 80 * LAKH,
         "property_type": "apartment"},
    ),
    (
        "Need a 3 BHK flat in Rajarhat, ready to move, budget 1.5 crore, home loan needed",
        {"bhk": [3], "locations": ["Rajarhat"], "timeline_months": 0, "loan": "needed",
         "budget_max_inr": 15 * CRORE // 10, "property_type": "apartment"},
    ),
    (
        "2 or 3 BHK for investment, possession within 6 months",
        {"bhk": [2, 3], "purpose": "investment", "timeline_months": 6},
    ),
    (
        "Looking for a villa, loan is already sanctioned",
        {"property_type": "villa", "loan": "pre_approved", "bhk": []},
    ),
    (
        "mujhe newtown mein do bhk chahiye, budget assi lakh tak",
        {"bhk": [2], "locations": ["New Town"], "budget_max_inr": 80 * LAKH},
    ),
    (
        "Hi, mujhe Rajarhat side pe 3 BHK chahiye, budget ek se dedh crore",
        {"bhk": [3], "locations": ["Rajarhat"], "budget_min_inr": CRORE,
         "budget_max_inr": 15 * CRORE // 10},
    ),
    (
        "rehne ke liye chahiye, loan nahi lenge, full payment",
        {"purpose": "self_use", "loan": "not_needed"},
    ),
    ("agle saal tak shift karna hai", {"timeline_months": 12}),
    ("dedh saal mein possession chalega", {"timeline_months": 18}),
    (
        "मुझे Behala में दो BHK फ्लैट चाहिए, बजट सत्तर लाख तक",
        {"bhk": [2], "locations": ["Behala"], "budget_max_inr": 70 * LAKH,
         "property_type": "apartment"},
    ),
    ("तुरंत चाहिए, होम लोन चाहिए", {"timeline_months": 0, "loan": "needed"}),
    ("निवेश के लिए प्लॉट देख रहे हैं", {"purpose": "investment", "property_type": "plot"}),
    ("6 महीने के अंदर", {"timeline_months": 6}),
    ("Just looking around for now", {"bhk": [], "locations": [], "budget_max_inr": None}),
]  # fmt: skip


@pytest.mark.parametrize(("text", "expected"), MESSAGES)
def test_messages(text: str, expected: dict[str, Any]) -> None:
    req = extract(text, localities=LOCALITIES).requirements
    for key, value in expected.items():
        assert getattr(req, key) == value, (key, req)


def test_property_type_inferred_from_bhk_is_recorded() -> None:
    result = extract("3 BHK chahiye", localities=LOCALITIES)
    assert result.requirements.property_type == "apartment"
    assert result.evidence["property_type"] == "inferred from BHK"


def test_location_aliases_and_no_false_matches() -> None:
    aliases = location_aliases(["New Town Action Area II", "Rajarhat"])
    assert "New Town" in aliases and "New" not in aliases
    renew = extract("I want to renew townhouse plans", localities=LOCALITIES)
    assert renew.requirements.locations == []
    assert extract("Kolkata mein", localities=LOCALITIES).requirements.locations == ["Kolkata"]


def test_phrasing_count() -> None:
    assert len(BUDGETS) + len(MESSAGES) >= 30
