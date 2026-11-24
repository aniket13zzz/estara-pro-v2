"""Customer-facing text from ``client/scripts/{lang}/`` (the only place it lives).

Files are read once per process and cached. HTML comments (reviewer notes such as "SAMPLE TEXT")
are removed, and ``{{company_name}}`` / ``{{agent_name}}`` are filled from config. An unknown
placeholder is an error, so a typo in a script never reaches a customer.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from app.core.config import ClientConfig

COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
OBJECTION_TYPES: tuple[str, ...] = (
    "price",
    "location",
    "think_about_it",
    "competitor",
    "loan",
    "trust",
)


@lru_cache(maxsize=256)
def _read(path: Path) -> str:
    return COMMENT.sub("", path.read_text(encoding="utf-8")).strip()


class Scripts:
    def __init__(self, config: ClientConfig) -> None:
        self.config = config
        self.root = config.scripts_root
        self.values = {"company_name": config.company.name, "agent_name": config.agent.name}

    def _render(self, text: str, source: str, extra: dict[str, str] | None = None) -> str:
        values = {**self.values, **(extra or {})}

        def fill(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in values:
                raise ValueError(f"{source}: unknown placeholder {{{{{key}}}}}")
            return values[key]

        return PLACEHOLDER.sub(fill, text)

    def text(self, language: str, name: str, **values: str) -> str:
        """A script file, e.g. ``text("hi", "objections/price.md")``. Templates such as
        ``follow_up.md`` take their per-customer values (``customer_name=...``)."""
        path = self.root / language / name
        source = f"{self.config.agent.scripts_dir}/{language}/{name}"
        return self._render(_read(path), source, values)

    def lines(self, language: str, name: str) -> list[str]:
        return [line.strip() for line in self.text(language, name).splitlines() if line.strip()]

    def objection(self, language: str, kind: str) -> str:
        if kind not in OBJECTION_TYPES:
            raise ValueError(f"unknown objection type {kind!r}")
        return self.text(language, f"objections/{kind}.md")

    def ai_disclosure(self, language: str) -> str:
        rel = self.config.compliance.ai_disclosure[language]
        return self._render(_read(self.config.client_dir / rel), rel)

    def paused_handoff(self, language: str) -> str:
        return self.text(language, "paused_handoff.md")

    def after_hours(self, language: str) -> str:
        return self.text(language, "after_hours.md")

    def sales_script(self, language: str) -> str:
        return self.text(language, "sales_script.md")

    def all_texts(self, language: str) -> list[str]:
        """Every script a reply may quote from, for the grounding guard's allowed sources."""
        names = ["sales_script.md", "paused_handoff.md", "after_hours.md"]
        names += [f"objections/{k}.md" for k in OBJECTION_TYPES]
        return [self.text(language, n) for n in names] + [self.ai_disclosure(language)]
