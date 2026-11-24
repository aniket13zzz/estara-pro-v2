"""The first thing a caller hears: short, in one language by default, with the recording notice."""

from __future__ import annotations

from app.brain.scripts import Scripts
from app.channels.vapi.assistant import first_message
from app.core.config import ClientConfig


def _with(config: ClientConfig, **agent: str) -> ClientConfig:
    return config.model_copy(update={"agent": config.agent.model_copy(update=agent)})


def test_default_greeting_is_one_language_with_the_notice(client_config: ClientConfig) -> None:
    scripts = Scripts(client_config)
    config = _with(client_config, greeting_mode="default_language", default_language="en")
    text = first_message(config, scripts)
    greeting = scripts.text("en", "greeting.md")
    notice = scripts.text("en", "recording_disclosure.md")
    assert text == f"{greeting} {notice}"
    assert scripts.text("hi", "greeting.md") not in text
    assert len(text) < 140  # short: a caller should be talking within a few seconds


def test_bilingual_greeting_gives_the_notice_in_every_language(client_config: ClientConfig) -> None:
    scripts = Scripts(client_config)
    for default in client_config.agent.languages:
        config = _with(client_config, greeting_mode="bilingual", default_language=default)
        text = first_message(config, scripts)
        for lang in config.agent.languages:
            assert scripts.text(lang, "recording_disclosure.md") in text
            assert scripts.text(lang, "greeting.md") in text
        assert text.startswith(scripts.text(default, "greeting.md"))
