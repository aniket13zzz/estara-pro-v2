"""Grounding guard, reply-language check and message signals (en, hi, Hinglish)."""

from __future__ import annotations

import pytest

from app.brain.grounding import Grounding, claims, language_ok
from app.brain.signals import detect_signals
from app.brain.tools import mask_commercial

TOOL_RESULT = {
    "matches": [
        {
            "project": "Sunrise Greens (Sample)",
            "price": "seventy two to seventy eight lakh rupees",
            "area": "820 square feet",
            "possession": "December 2027",
        }
    ],
    "slots": [{"slot_id": "2026-10-17T11:00", "slot": "Saturday 17 October at 11 am"}],
}
TOOL_RESULT_HI = {
    "price": "बहत्तर से अठहत्तर लाख रुपये",
    "slot": "शनिवार 17 अक्टूबर, दोपहर दो बजे",
}


@pytest.fixture
def grounding() -> Grounding:
    g = Grounding()
    g.add_json(TOOL_RESULT)
    g.add_json(TOOL_RESULT_HI)
    g.add_text("mujhe 2 bhk chahiye, budget 80 lakh tak, 6 mahine mein")
    return g


@pytest.mark.parametrize(
    "reply",
    [
        "The 2 BHK at Sunrise Greens is 72 to 78 lakh rupees.",
        "It starts at seventy two lakh rupees and the carpet area is 820 sq ft.",
        "Possession is December 2027.",
        "Shall I request Saturday 17 October at 11 am?",
        "Within your 80 lakh budget, here is one option.",
        "इसकी कीमत बहत्तर से अठहत्तर लाख रुपये है।",
        "शनिवार 17 अक्टूबर, दोपहर दो बजे ठीक रहेगा?",
        "Aapke 6 mahine ke timeline mein ye ready ho sakta hai.",
        "I have 2 questions for you.",  # small plain numbers are not claims
    ],
)
def test_grounded_replies_pass(grounding: Grounding, reply: str) -> None:
    assert grounding.ungrounded(reply) == []


@pytest.mark.parametrize(
    ("reply", "kind"),
    [
        ("It costs 75 lakh rupees.", "money"),
        ("It costs ₹75,00,000.", "money"),
        ("Price is around 1.2 crore.", "money"),
        ("इसकी कीमत पचहत्तर लाख रुपये है।", "money"),
        ("There is a 5% discount.", "percent"),
        ("Possession is March 2027.", "date"),
        ("You could visit on 18 October.", "date"),
        ("How about 4 pm?", "time"),
        ("शाम चार बजे आ जाइए।", "time"),
        ("It will be ready in 18 months.", "duration"),
        ("Only 3 units left!", "count"),
        ("The flat is 950 square feet.", "area"),
    ],
)
def test_ungrounded_claims_are_caught(grounding: Grounding, reply: str, kind: str) -> None:
    bad = grounding.ungrounded(reply)
    assert kind in {c.kind for c in bad}, bad


def test_strip_removes_only_ungrounded_sentences(grounding: Grounding) -> None:
    text, removed = grounding.remove_ungrounded(
        "Lovely choice. It is 75 lakh. Shall I share slots?"
    )
    assert text == "Lovely choice. Shall I share slots?"
    assert removed == ["It is 75 lakh."]
    hi, removed_hi = grounding.remove_ungrounded("बढ़िया। इसकी कीमत पचहत्तर लाख रुपये है। visit करेंगे?")
    assert hi == "बढ़िया। visit करेंगे?" and len(removed_hi) == 1


def test_claims_ignore_bhk_and_small_numbers_but_not_counts() -> None:
    assert claims("3 BHK, step 1, two questions, 12th floor") == []
    assert [c.kind for c in claims("I found 2 options")] == ["count"]


def test_script_list_markers_are_not_allowed_values() -> None:
    g = Grounding()
    g.add_text("1. Ask. 5. Search. Budget like 80 lakh.", plain_numbers=False)
    assert g.ungrounded("5% off") and not g.ungrounded("80 lakh")


@pytest.mark.parametrize(
    ("text", "language", "ok"),
    [
        ("Sure, which area do you prefer?", "en", True),
        ("ज़रूर, आपको कौन सा area पसंद है?", "hi", True),
        ("Sunrise Greens में 2 BHK option है।", "hi", True),
        ("Sure, which area do you prefer?", "hi", False),
        ("aapko kaunsa area pasand hai?", "hi", False),  # Hindi replies are in Devanagari
        ("ठीक है", "en", False),
    ],
)
def test_reply_language(text: str, language: str, ok: bool) -> None:
    assert language_ok(text, language) is ok


@pytest.mark.parametrize(
    ("text", "objection", "escalation", "asked"),
    [
        ("This is too expensive for me", "price", None, False),
        ("ye to bahut mehenga hai", "price", None, False),
        ("यह बहुत महंगा है", "price", None, False),
        ("It's too far from my office", "location", None, False),
        ("बहुत दूर है", "location", None, False),
        ("let me think about it", "think_about_it", None, False),
        ("family se baat karke batata hoon, baad mein", "think_about_it", None, False),
        ("another builder gave me a better deal", "competitor", None, False),
        ("I want to talk to a real person", None, "human_requested", False),
        ("kisi insaan se baat karao", None, "human_requested", False),
        ("मुझे किसी से बात करनी है", None, "human_requested", False),
        ("I will send a legal notice", None, "legal", False),
        ("मुझे वकील से पूछना है", None, "legal", False),
        ("this is useless, worst service", None, "angry", False),
        ("bakwas service hai", None, "angry", False),
        ("what's the cricket score", None, "out_of_scope", False),
        ("are you a real person?", None, None, True),
        ("kya aap robot ho?", None, None, True),
        ("क्या आप इंसान हैं?", None, None, True),
        ("is there any discount on 2 BHK", None, None, False),
        ("fir batao kya options hain", None, None, False),
    ],
)
def test_signals(text: str, objection: str | None, escalation: str | None, asked: bool) -> None:
    s = detect_signals(text)
    assert (s.objection, s.escalation, s.asked_if_ai) == (objection, escalation, asked)


def test_documents_never_supply_prices_or_dates() -> None:
    text = "Homes from Rs. 72,00,000 and 1.2 crore, 10% off, possession Dec 2027. 18 floors."
    masked = mask_commercial(text)
    assert "72,00,000" not in masked and "1.2 crore" not in masked and "10%" not in masked
    assert "2027" not in masked and "18 floors" in masked
