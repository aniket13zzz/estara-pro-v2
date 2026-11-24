"""Logging setup with redaction of phone numbers, emails and tokens.

Every log record passes through ``RedactingFilter``. Full structured JSON logging with request IDs
is a Stage 8 item; redaction is in place from the start.
"""

from __future__ import annotations

import logging
import re

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Bearer / API tokens and long secrets first, so they are not partially matched below.
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [REDACTED_TOKEN]"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|token|secret|password|authorization)(\"?\s*[:=]\s*\"?)[^\s\",;]+"
        ),
        r"\1\2[REDACTED]",
    ),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
    # Phone numbers: E.164 or 10+ digit runs with optional separators.
    (re.compile(r"\+?\d[\d\s-]{8,}\d"), "[REDACTED_PHONE]"),
)


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        record.msg = redact(message)
        record.args = None
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
