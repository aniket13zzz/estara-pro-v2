"""Deterministic language detection: English, Hindi (Devanagari) and Hinglish (Roman)."""

from __future__ import annotations

import pytest

from app.conversations.language import choose_language, detect, reply_language

LANGS = ["en", "hi"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # English
        ("I am looking for a 2 BHK flat in New Town", "en"),
        ("What is the price of the 3 BHK at Sunrise Greens?", "en"),
        ("Sir I want something near the airport, budget around 80 lakh", "en"),
        ("Can I speak to a person please", "en"),
        # Hindi (Devanagari)
        ("मुझे दो BHK चाहिए", "hi"),
        ("बजट अस्सी लाख तक है", "hi"),
        ("New Town में कुछ दिखाइए", "hi"),
        ("Sunrise Lakeview में कौन से payment plan हैं?", "hi"),  # mostly Latin letters
        # Hinglish (Roman)
        ("mujhe 2 bhk chahiye new town mein", "hi"),
        ("budget ek se dedh crore hai", "hi"),
        ("kitne ka hai ye flat?", "hi"),
        ("haan ji, loan chahiye", "hi"),
        ("dusre builder ne isse sasta offer diya hai", "hi"),
        ("aaj cricket match kaun jeeta?", "hi"),
        ("I need a home loan too, is that possible", "en"),
    ],
)
def test_detect(text: str, expected: str) -> None:
    result = detect(text)
    assert result.language == expected, result
    assert result.confident


@pytest.mark.parametrize("text", ["ok", "2 BHK", "yes", "hello", "Rahul"])
def test_short_neutral_messages_are_not_confident(text: str) -> None:
    assert not detect(text).confident


def test_choose_language_keeps_current_on_neutral_text() -> None:
    assert choose_language("ok", current="hi", languages=LANGS, default_language="en") == (
        "hi",
        False,
    )


def test_choose_language_records_a_switch() -> None:
    lang, switched = choose_language(
        "Can you tell me about the payment plan?",
        current="hi",
        languages=LANGS,
        default_language="hi",
    )
    assert (lang, switched) == ("en", True)


def test_first_message_is_not_a_switch() -> None:
    assert choose_language(
        "मुझे फ्लैट चाहिए", current=None, languages=LANGS, default_language="en"
    ) == ("hi", False)


def test_unconfigured_language_is_ignored() -> None:
    # An English-only client never switches to Hindi.
    assert choose_language(
        "मुझे फ्लैट चाहिए", current="en", languages=["en"], default_language="en"
    ) == ("en", False)


def test_reply_language() -> None:
    assert reply_language("ठीक है, New Town में दो BHK देख लेती हूँ") == "hi"
    assert reply_language("Sure, I can show you two options in New Town.") == "en"
