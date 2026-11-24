"""Transcribers sometimes write a spoken lakh amount as dollars: the rules must not save it."""

from app.brain.extraction import extract_budget


def test_dollar_transcripts_are_never_saved_as_a_budget() -> None:
    for text in ("My budget is $80,000.", "80,000 dollars", "budget is $8,00,000"):
        assert extract_budget(text) == (None, None), text
    assert extract_budget("My budget is 80 lakh") == (None, 8_000_000)
