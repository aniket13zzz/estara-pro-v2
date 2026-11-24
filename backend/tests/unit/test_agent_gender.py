"""Hindi verb forms follow agent.gender, so a male agent never speaks in feminine forms."""

from __future__ import annotations

import pytest

from app.brain.prompt import TurnNotes, build_system_prompt
from app.brain.scripts import Scripts
from app.core.config import ClientConfig
from app.core.enums import Channel


@pytest.mark.parametrize(("gender", "form"), [("female", "करूँगी"), ("male", "करूँगा")])
def test_hindi_prompt_uses_the_agents_gender(
    client_config: ClientConfig, gender: str, form: str
) -> None:
    agent = client_config.agent.model_copy(update={"gender": gender})
    config = client_config.model_copy(update={"agent": agent})
    notes = TurnNotes(lead_state="new_enquiry", qualified=False, missing=[], known={})
    hindi = build_system_prompt(config, Scripts(config), language="hi", channel=Channel.VOICE,
                                notes=notes)  # fmt: skip
    english = build_system_prompt(config, Scripts(config), language="en", channel=Channel.VOICE,
                                  notes=notes)  # fmt: skip
    assert f"You are {gender}" in hindi and form in hindi
    assert "verb forms" not in english
