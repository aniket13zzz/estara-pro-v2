"""Log redaction for phone numbers, emails and tokens (CLAUDE.md security baseline)."""

from __future__ import annotations

import logging

import pytest

from app.core.logging import RedactingFilter, redact


@pytest.mark.parametrize(
    ("raw", "leak"),
    [
        ("caller +919000000001 asked about 2 BHK", "+919000000001"),
        ("call from 98300 12345 ended", "98300 12345"),
        ("email rahul@example.com bounced", "rahul@example.com"),
        ("Authorization: Bearer abcdefghijklmnop123", "abcdefghijklmnop123"),
        ("vapi api_key=sk_live_123456789", "sk_live_123456789"),  # gitleaks:allow (fake)
        ('{"password": "hunter2hunter2"}', "hunter2hunter2"),
    ],
)
def test_redact_removes_personal_data_and_secrets(raw: str, leak: str) -> None:
    assert leak not in redact(raw)


def test_redact_keeps_ordinary_numbers() -> None:
    assert redact("2 BHK, 3 options, slot 11:00") == "2 BHK, 3 options, slot 11:00"


def test_filter_redacts_formatted_args() -> None:
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1, "lead %s phone %s", ("x", "+919000000001"), None
    )
    RedactingFilter().filter(record)
    assert "+919000000001" not in record.getMessage()
