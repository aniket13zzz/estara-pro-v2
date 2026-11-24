"""Fixes from the first full live call (2026-11-14)."""

from app.channels.vapi.gateway import _numbers
from app.matching.service import location_matches

NEW_TOWN = "New Town Action Area II"


def test_misheard_place_names_still_match() -> None:
    for heard in ("Newton", "newtown", "New Town", "new town"):
        assert location_matches(heard, NEW_TOWN, "Kolkata") == "locality", heard
    assert location_matches("Rajarhaat", "Rajarhat", "Kolkata") == "locality"


def test_unrelated_or_short_names_do_not_match() -> None:
    assert location_matches("Bihar", "Behala", "Kolkata") is None
    assert location_matches("Salt Lake", NEW_TOWN, "Kolkata") is None
    assert location_matches("Baruipur", "Behala", "Kolkata") is None


def test_clock_times_are_not_flagged_as_numbers() -> None:
    assert _numbers("Sunday the 15th of November at 2:00 PM") == {"15"}
    assert _numbers("at 11:00 AM or 2:00 PM") == {"11"}
    assert _numbers("72 to 78 lakh rupees") == {"72", "78"}
