"""Config validation: valid config, missing Hindi files, bad phones, bad slots and edge rules."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.core.config import REQUIRED_SCRIPT_FILES, ConfigError, load_config
from app.core.enums import FactType
from tests.conftest import BACKEND_DIR, CLIENT_DIR, TEST_SECRET


def _load_yaml(client_dir: Path) -> dict[str, Any]:
    data = yaml.safe_load((client_dir / "client.config.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _write_yaml(client_dir: Path, data: dict[str, Any]) -> Path:
    path = client_dir / "client.config.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def _expect_error(client_dir: Path, *fragments: str) -> str:
    with pytest.raises(ConfigError) as exc_info:
        load_config(client_dir / "client.config.yaml")
    message = str(exc_info.value)
    for fragment in fragments:
        assert fragment in message, f"{fragment!r} not in:\n{message}"
    return message


# ------------------------------------------------------------------------------------ valid


def test_sample_config_is_valid() -> None:
    cfg = load_config(CLIENT_DIR / "client.config.yaml")
    assert cfg.agent.languages == ["en", "hi"]
    assert cfg.agent.default_language == "en"  # short English greeting; Sara follows the caller
    assert cfg.agent.greeting_mode == "default_language"
    assert cfg.follow_up.channels == ["whatsapp"]
    assert [f.value for f in cfg.truth.block_on_stale] == [
        "price",
        "availability",
        "offer",
        "payment_plan",
        "possession",
        "area",
        "amenity",
    ]
    # Every fact type is blocked when stale in the sample config.
    assert {f.value for f in cfg.truth.block_on_stale} == {f.value for f in FactType}


def test_custom_llm_voice_needs_every_voice_field(client_copy: Path) -> None:
    cfg = load_config(client_copy / "client.config.yaml")
    assert cfg.voice.mode == "native"  # the sample client uses Vapi's own model (ADR 0014)

    data = _load_yaml(client_copy)
    data["voice"]["mode"] = "custom_llm"
    cfg = load_config(_write_yaml(client_copy, data))
    assert cfg.voice.custom_ready is False
    data["voice"]["tts"].update(model="sonic-model", voice_id="voice-123")
    data["voice"]["stt"].update(provider="deepgram", model="nova")
    cfg = load_config(_write_yaml(client_copy, data))
    assert cfg.voice.custom_ready is True


def test_hindi_scripts_are_devanagari_and_present() -> None:
    for rel in REQUIRED_SCRIPT_FILES:
        text = (CLIENT_DIR / "scripts" / "hi" / rel).read_text(encoding="utf-8")
        assert any("ऀ" <= ch <= "ॿ" for ch in text), f"hi/{rel} has no Devanagari"


# ------------------------------------------------------------------------- missing files


def test_missing_hindi_script_file_fails(client_copy: Path) -> None:
    (client_copy / "scripts" / "hi" / "objections" / "price.md").unlink()
    _expect_error(client_copy, "scripts/hi/objections/price.md not found")


def test_empty_hindi_script_file_fails(client_copy: Path) -> None:
    (client_copy / "scripts" / "hi" / "greeting.md").write_text("", encoding="utf-8")
    _expect_error(client_copy, "scripts/hi/greeting.md is empty")


def test_missing_hindi_whatsapp_template_fails(client_copy: Path) -> None:
    (client_copy / "scripts" / "hi" / "whatsapp" / "visit_confirmation.md").unlink()
    _expect_error(
        client_copy,
        "scripts/hi/whatsapp/visit_confirmation.md not found",
        "same WhatsApp templates",
    )


def test_missing_disclosure_path_fails(client_copy: Path) -> None:
    data = _load_yaml(client_copy)
    data["compliance"]["recording_disclosure"]["hi"] = "scripts/hi/missing.md"
    _write_yaml(client_copy, data)
    _expect_error(client_copy, "compliance.recording_disclosure.hi")


def test_disclosure_must_cover_every_language(client_copy: Path) -> None:
    data = _load_yaml(client_copy)
    del data["compliance"]["ai_disclosure"]["hi"]
    _write_yaml(client_copy, data)
    _expect_error(client_copy, "compliance.ai_disclosure must have exactly the languages")


def test_missing_config_file_fails(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Client config not found"):
        load_config(tmp_path / "nope.yaml")


# ---------------------------------------------------------------------------------- phones


@pytest.mark.parametrize("bad", ["+91XXXXXXXXXX", "9000000001", "+91 90000 00001", "+0123456789"])
def test_bad_transfer_number_fails(client_copy: Path, bad: str) -> None:
    data = _load_yaml(client_copy)
    data["escalation"]["human_transfer_number"] = bad
    _write_yaml(client_copy, data)
    _expect_error(client_copy, "escalation.human_transfer_number", "not E.164")


def test_bad_sales_team_phone_fails(client_copy: Path) -> None:
    data = _load_yaml(client_copy)
    data["sales_team"][0]["phone"] = "+91-9000000001"
    _write_yaml(client_copy, data)
    _expect_error(client_copy, "sales_team.0.phone", "not E.164")


# ----------------------------------------------------------------------------------- slots


@pytest.mark.parametrize("slot", ["25:00", "9am", "11:60", "1100"])
def test_bad_visit_slot_fails(client_copy: Path, slot: str) -> None:
    data = _load_yaml(client_copy)
    data["site_visits"]["slots"] = ["11:00", slot]
    _write_yaml(client_copy, data)
    _expect_error(client_copy, "site_visits.slots")


def test_duplicate_slot_fails(client_copy: Path) -> None:
    data = _load_yaml(client_copy)
    data["site_visits"]["slots"] = ["11:00", "11:00"]
    _write_yaml(client_copy, data)
    _expect_error(client_copy, "duplicates")


def test_working_hours_must_be_ordered(client_copy: Path) -> None:
    data = _load_yaml(client_copy)
    data["escalation"]["working_hours"].update(start="19:00", end="10:00")
    _write_yaml(client_copy, data)
    _expect_error(client_copy, "must be before end")


# ------------------------------------------------------------------------------ edge rules


def test_thresholds_must_be_ordered(client_copy: Path) -> None:
    data = _load_yaml(client_copy)
    data["lead_scoring"]["thresholds"] = {"hot": 40, "warm": 70}
    _write_yaml(client_copy, data)
    _expect_error(client_copy, "0 < warm < hot <= 100")


def test_voice_follow_up_rejected_while_outbound_off(client_copy: Path) -> None:
    data = _load_yaml(client_copy)
    data["follow_up"]["channels"] = ["whatsapp", "voice"]
    _write_yaml(client_copy, data)
    _expect_error(client_copy, "outbound_calls_enabled is false")


def test_voice_follow_up_allowed_when_outbound_on(client_copy: Path) -> None:
    data = _load_yaml(client_copy)
    data["follow_up"]["channels"] = ["whatsapp", "voice"]
    data["compliance"]["outbound_calls_enabled"] = True
    cfg = load_config(_write_yaml(client_copy, data))
    assert "voice" in cfg.follow_up.channels


def test_embedding_dimensions_fixed_at_768(client_copy: Path) -> None:
    data = _load_yaml(client_copy)
    data["embeddings"]["dimensions"] = 1536
    _write_yaml(client_copy, data)
    _expect_error(client_copy, "pgvector column is 768")


def test_default_language_must_be_supported(client_copy: Path) -> None:
    data = _load_yaml(client_copy)
    data["agent"]["default_language"] = "ta"
    _write_yaml(client_copy, data)
    _expect_error(client_copy, "default_language 'ta' is not in languages")


def test_opt_out_keywords_needed_for_every_language(client_copy: Path) -> None:
    data = _load_yaml(client_copy)
    del data["channels"]["whatsapp"]["opt_out_keywords"]["hi"]
    _write_yaml(client_copy, data)
    _expect_error(client_copy, "opt_out_keywords must have exactly the languages")


def test_unknown_fact_type_fails(client_copy: Path) -> None:
    data = _load_yaml(client_copy)
    data["truth"]["block_on_stale"] = ["price", "offers"]
    _write_yaml(client_copy, data)
    _expect_error(client_copy, "truth.block_on_stale")


def test_unknown_key_is_rejected(client_copy: Path) -> None:
    data = _load_yaml(client_copy)
    data["agent"]["voicee"] = "typo"
    _write_yaml(client_copy, data)
    _expect_error(client_copy, "agent.voicee")


def test_every_problem_is_reported_at_once(client_copy: Path) -> None:
    data = _load_yaml(client_copy)
    data["escalation"]["human_transfer_number"] = "12345"
    data["site_visits"]["slots"] = ["9am"]
    _write_yaml(client_copy, data)
    (client_copy / "scripts" / "hi" / "objections" / "competitor.md").unlink()
    message = _expect_error(
        client_copy,
        "escalation.human_transfer_number",
        "site_visits.slots",
        "scripts/hi/objections/competitor.md not found",
    )
    assert "3 problem(s)" in message


# -------------------------------------------------------------------------------- startup


def _start_app(env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **env_overrides}
    return subprocess.run(
        [sys.executable, "-c", "import app.asgi"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_invalid_config_stops_startup_with_clear_error(client_copy: Path) -> None:
    (client_copy / "scripts" / "hi" / "after_hours.md").unlink()
    result = _start_app(
        {
            "ESTARA_CONFIG_PATH": str(client_copy / "client.config.yaml"),
            "SESSION_SECRET": TEST_SECRET,
        }
    )
    assert result.returncode == 1
    assert "Invalid client config" in result.stderr
    assert "scripts/hi/after_hours.md not found" in result.stderr
    assert "Traceback" not in result.stderr


def test_missing_session_secret_stops_startup(tmp_path: Path) -> None:
    result = _start_app(
        {"ESTARA_CONFIG_PATH": str(CLIENT_DIR / "client.config.yaml"), "SESSION_SECRET": "short"}
    )
    assert result.returncode == 1
    assert "SESSION_SECRET must be set" in result.stderr


def test_valid_config_starts(client_copy: Path) -> None:
    result = _start_app(
        {
            "ESTARA_CONFIG_PATH": str(client_copy / "client.config.yaml"),
            "SESSION_SECRET": TEST_SECRET,
        }
    )
    assert result.returncode == 0, result.stderr
