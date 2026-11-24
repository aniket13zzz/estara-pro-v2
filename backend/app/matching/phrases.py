"""Words used to build match reasons, per language.

Like the spoken strings in ``app/truth/formatters.py``, these are tool-result vocabulary for
the LLM to read and rephrase, not client scripts; every amount and date inside a reason comes
from the Truth Layer. A language listed in ``agent.languages`` needs an entry here (checked at
startup by ``check_languages``).
"""

from __future__ import annotations

PHRASES: dict[str, dict[str, str]] = {
    "en": {
        "option": "{name} at {project}, {locality}",
        "within_budget": "within your budget",
        "above_budget": "slightly above your budget",
        "preferred_area": "in your preferred area",
        "other_area": "in {locality}, not in {wanted}",
        "ready": "ready to move",
        "possession_ok": "possession {when}, within your timeline",
        "possession_late": "possession {when}, later than your timeline",
        "possession": "possession {when}",
        "available": "{count} available",
        "price_tbc": "price to be confirmed by the sales team",
    },
    "hi": {
        "option": "{project}, {locality} में {name}",
        "within_budget": "आपके बजट में",
        "above_budget": "आपके बजट से थोड़ा ऊपर",
        "preferred_area": "आपके पसंद के area में",
        "other_area": "{locality} में है, {wanted} में नहीं",
        "ready": "ready to move",
        "possession_ok": "possession {when}, आपकी timeline के अंदर",
        "possession_late": "possession {when}, आपकी timeline के बाद",
        "possession": "possession {when}",
        "available": "{count} उपलब्ध",
        "price_tbc": "price sales team confirm करेगी",
    },
}


def check_languages(languages: list[str]) -> None:
    missing = [lang for lang in languages if lang not in PHRASES]
    if missing:
        raise ValueError(f"no match-reason phrases for languages {missing}")
