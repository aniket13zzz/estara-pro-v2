"""Client configuration: schema, loader and validation.

``client/client.config.yaml`` is the only per-client behaviour switch. It is validated here at
startup; any problem stops the app with a readable list of every error found.

Validation rules come from docs/roadmap/phase-plan.md ("Validation rules that settle edge cases").
"""

from __future__ import annotations

import os
import re
from datetime import time
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)

from app.core.enums import (
    EMBEDDING_DIMENSIONS,
    REQUIREMENT_FIELDS,
    SCORING_WEIGHT_KEYS,
    FactType,
    Role,
)

# --------------------------------------------------------------------------------------------
# Constants

E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
HHMM_RE = re.compile(r"^\d{2}:\d{2}$")
LANG_RE = re.compile(r"^[a-z]{2}$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

# Script files every language must have (phase-plan.md, "Script files"). whatsapp/ is checked
# separately: every language must contain the same, non-empty set of template names.
REQUIRED_SCRIPT_FILES: tuple[str, ...] = (
    "greeting.md",
    "ai_disclosure.md",
    "recording_disclosure.md",
    "sales_script.md",
    "objections/price.md",
    "objections/location.md",
    "objections/think_about_it.md",
    "objections/competitor.md",
    "filler_lines.md",
    "follow_up.md",
    "paused_handoff.md",
    "after_hours.md",
    "voice_transfer.md",
    "whatsapp_unsupported.md",
    "customer_name_fallback.md",
)
WHATSAPP_TEMPLATE_DIR = "whatsapp"

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class ConfigError(Exception):
    """Raised when the client config is missing or invalid. The message lists every problem."""


# --------------------------------------------------------------------------------------------
# Helpers


def _check_e164(value: str) -> str:
    if not E164_RE.match(value):
        raise ValueError(f"phone number {value!r} is not E.164 (expected e.g. +919000000001)")
    return value


def _parse_hhmm(value: str) -> time:
    if not HHMM_RE.match(value):
        raise ValueError(f"time {value!r} must be HH:MM (24-hour)")
    hours, minutes = (int(p) for p in value.split(":"))
    if hours > 23 or minutes > 59:
        raise ValueError(f"time {value!r} is not a valid time of day")
    return time(hours, minutes)


Phone = Annotated[str, Field(min_length=1)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------------------------
# Sections


class Company(_Strict):
    name: str = Field(min_length=1)
    timezone: str
    currency: Literal["INR"]

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {v!r}") from exc
        return v


class Agent(_Strict):
    name: str = Field(min_length=1)
    # Hindi verbs change with the speaker's gender ("बताती हूँ" / "बताता हूँ"). Match the voice
    # and the Hindi scripts.
    gender: Literal["female", "male"]
    tone: str = Field(min_length=1)
    languages: list[str] = Field(min_length=1)
    default_language: str
    greeting_mode: Literal["bilingual", "default_language"]
    scripts_dir: str = Field(min_length=1)

    @field_validator("languages")
    @classmethod
    def _langs(cls, v: list[str]) -> list[str]:
        for lang in v:
            if not LANG_RE.match(lang):
                raise ValueError(f"language code {lang!r} must be a two-letter code like 'en'")
        if len(set(v)) != len(v):
            raise ValueError("languages contains duplicates")
        return v

    @model_validator(mode="after")
    def _default_in_languages(self) -> Agent:
        if self.default_language not in self.languages:
            raise ValueError(
                f"default_language {self.default_language!r} is not in languages {self.languages}"
            )
        if self.greeting_mode == "bilingual" and len(self.languages) < 2:
            raise ValueError("greeting_mode 'bilingual' needs at least two languages")
        return self


class TTSConfig(_Strict):
    provider: Literal["cartesia"]
    model: str | None = None  # null until the client's voice is chosen (docs/demo.md)
    voice_id: str | None = None  # null until the client's voice is chosen (docs/demo.md)


class STTConfig(_Strict):
    provider: str | None = None  # set with the voice (docs/demo.md); null = no live assistant
    model: str | None = None
    language: str = "multi"


class Voice(_Strict):
    provider: Literal["vapi"]
    # native: Vapi's own model talks and calls ESTARA's tools (model, voice and transcriber are
    # chosen in the Vapi dashboard). custom_llm: every reply comes from ESTARA's Sales Brain
    # (tts and stt below). ADR 0014.
    mode: Literal["native", "custom_llm"] = "custom_llm"
    tts: TTSConfig
    stt: STTConfig
    filler_lines: bool
    max_call_minutes: int = Field(gt=0, le=120)

    @property
    def custom_ready(self) -> bool:
        """custom_llm mode: every voice field set, so ESTARA can build the whole assistant."""
        return None not in (self.tts.model, self.tts.voice_id, self.stt.provider, self.stt.model)


class LLMPricing(_Strict):
    """List price in USD per million tokens, used to turn provider token counts into cost."""

    input_usd_per_mtok: float = Field(ge=0)
    output_usd_per_mtok: float = Field(ge=0)


class LLM(_Strict):
    provider: Literal["gemini", "claude", "openai"]
    model: str = Field(min_length=1)
    temperature: float = Field(ge=0, le=2)
    pricing: LLMPricing | None = None  # null: tokens are recorded, cost is left empty
    # Gemini only (generationConfig.thinkingConfig.thinkingLevel); null = the model's default.
    # Other providers ignore it.
    thinking_level: Literal["low", "medium", "high"] | None = None


class Embeddings(_Strict):
    provider: Literal["gemini", "openai", "local"]
    model: str = Field(min_length=1)
    dimensions: int

    @field_validator("dimensions")
    @classmethod
    def _dims(cls, v: int) -> int:
        if v != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"embeddings.dimensions is {v}, but the template's pgvector column is "
                f"{EMBEDDING_DIMENSIONS}. Choose a model that outputs {EMBEDDING_DIMENSIONS} "
                "dimensions; changing the column is a template change, not a config change."
            )
        return v


class WhatsApp(_Strict):
    # WhatsApp is on when its keys are in .env (docs/deploy.md); this holds only the wording rules.
    opt_out_keywords: dict[str, list[str]]

    @field_validator("opt_out_keywords")
    @classmethod
    def _keywords(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        for lang, words in v.items():
            if not words or any(not w.strip() for w in words):
                raise ValueError(f"opt_out_keywords.{lang} must be a list of non-empty keywords")
        return v


class Channels(_Strict):
    whatsapp: WhatsApp


class SalesPerson(_Strict):
    name: str = Field(min_length=1)
    phone: Phone
    email: EmailStr
    role: Role
    languages: list[str] = Field(min_length=1)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return _check_e164(v)


class WorkingHours(_Strict):
    days: list[Weekday] = Field(min_length=1)
    start: str
    end: str

    @model_validator(mode="after")
    def _order(self) -> WorkingHours:
        start, end = _parse_hhmm(self.start), _parse_hhmm(self.end)
        if start >= end:
            raise ValueError(f"working_hours start {self.start} must be before end {self.end}")
        return self


class Escalation(_Strict):
    human_transfer_number: Phone
    working_hours: WorkingHours
    after_hours: Literal["take_details_and_callback", "transfer_to_human"]

    @field_validator("human_transfer_number")
    @classmethod
    def _phone(cls, v: str) -> str:
        return _check_e164(v)


class SiteVisits(_Strict):
    days: list[Weekday] = Field(min_length=1)
    slots: list[str] = Field(min_length=1)
    min_lead_hours: int = Field(ge=0)
    max_days_ahead: int = Field(ge=1)
    slot_capacity: int = Field(ge=1)
    confirmed_by: list[Role] = Field(min_length=1)

    @field_validator("slots")
    @classmethod
    def _slots(cls, v: list[str]) -> list[str]:
        for slot in v:
            _parse_hhmm(slot)
        if len(set(v)) != len(v):
            raise ValueError("site_visits.slots contains duplicates")
        return v

    @model_validator(mode="after")
    def _window(self) -> SiteVisits:
        if self.min_lead_hours >= self.max_days_ahead * 24:
            raise ValueError("min_lead_hours must be shorter than max_days_ahead")
        return self


class Thresholds(_Strict):
    hot: int
    warm: int

    @model_validator(mode="after")
    def _ordered(self) -> Thresholds:
        if not (0 < self.warm < self.hot <= 100):
            raise ValueError(
                f"lead_scoring thresholds must satisfy 0 < warm < hot <= 100 "
                f"(got warm={self.warm}, hot={self.hot})"
            )
        return self


class LeadScoring(_Strict):
    weights: dict[str, int]
    thresholds: Thresholds
    qualified_requires: list[str] = Field(min_length=1)

    @field_validator("weights")
    @classmethod
    def _weights(cls, v: dict[str, int]) -> dict[str, int]:
        unknown = set(v) - SCORING_WEIGHT_KEYS
        if unknown:
            raise ValueError(
                f"unknown scoring weights {sorted(unknown)}; allowed: {sorted(SCORING_WEIGHT_KEYS)}"
            )
        if any(w < 0 for w in v.values()):
            raise ValueError("scoring weights must not be negative")
        if sum(v.values()) <= 0:
            raise ValueError("scoring weights must add up to more than 0")
        return v

    @field_validator("qualified_requires")
    @classmethod
    def _requires(cls, v: list[str]) -> list[str]:
        unknown = set(v) - REQUIREMENT_FIELDS
        if unknown:
            raise ValueError(
                f"unknown requirement fields {sorted(unknown)}; "
                f"allowed: {sorted(REQUIREMENT_FIELDS)}"
            )
        return v


class FollowUp(_Strict):
    schedule_days: list[int] = Field(min_length=1)
    channels: list[Literal["whatsapp", "voice"]] = Field(min_length=1)

    @field_validator("schedule_days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if any(d < 1 for d in v) or v != sorted(set(v)):
            raise ValueError("follow_up.schedule_days must be positive, unique and ascending")
        return v


class Compliance(_Strict):
    ai_disclosure: dict[str, str]
    recording_disclosure: dict[str, str]
    outbound_calls_enabled: bool


class Truth(_Strict):
    stale_after_days: int = Field(ge=1)
    block_on_stale: list[FactType] = Field(min_length=1)


class Retention(_Strict):
    transcript_days: int = Field(ge=1)
    recording_days: int = Field(ge=1)


class UsageLimits(_Strict):
    monthly_call_minutes: int = Field(gt=0)
    monthly_llm_usd: float = Field(gt=0)
    alert_at_percent: list[int] = Field(min_length=1)

    @field_validator("alert_at_percent")
    @classmethod
    def _alerts(cls, v: list[int]) -> list[int]:
        if any(p < 1 or p > 100 for p in v) or v != sorted(set(v)):
            raise ValueError("alert_at_percent must be unique, ascending values between 1 and 100")
        return v


# --------------------------------------------------------------------------------------------
# Root


class ClientConfig(_Strict):
    template_version: str
    tier: Literal["pro"]
    company: Company
    agent: Agent
    voice: Voice
    llm: LLM
    embeddings: Embeddings
    channels: Channels
    sales_team: list[SalesPerson] = Field(min_length=1)
    escalation: Escalation
    site_visits: SiteVisits
    lead_scoring: LeadScoring
    follow_up: FollowUp
    compliance: Compliance
    truth: Truth
    retention: Retention
    usage_limits: UsageLimits
    # Folder the config was loaded from; script paths resolve against it.
    _client_dir: Path | None = PrivateAttr(default=None)

    @field_validator("template_version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not SEMVER_RE.match(v):
            raise ValueError(f"template_version {v!r} must look like 1.0.0")
        return v

    @model_validator(mode="after")
    def _cross_section(self) -> ClientConfig:
        langs = set(self.agent.languages)
        problems: list[str] = []

        for person in self.sales_team:
            extra = set(person.languages) - langs
            if extra:
                problems.append(
                    f"sales_team member {person.name!r} speaks {sorted(extra)}, "
                    f"which are not in agent.languages {self.agent.languages}"
                )

        kw_langs = set(self.channels.whatsapp.opt_out_keywords)
        if kw_langs != langs:
            problems.append(
                f"channels.whatsapp.opt_out_keywords must have exactly the languages "
                f"{sorted(langs)} (got {sorted(kw_langs)})"
            )

        for section in ("ai_disclosure", "recording_disclosure"):
            keys = set(getattr(self.compliance, section))
            if keys != langs:
                problems.append(
                    f"compliance.{section} must have exactly the languages {sorted(langs)} "
                    f"(got {sorted(keys)})"
                )

        if "voice" in self.follow_up.channels and not self.compliance.outbound_calls_enabled:
            problems.append(
                "follow_up.channels contains 'voice', but compliance.outbound_calls_enabled is "
                "false. Voice follow-ups are outbound calls; remove 'voice' (follow-ups go via "
                "WhatsApp) or enable outbound calls after a DND/TRAI review."
            )

        if problems:
            raise ValueError("; ".join(problems))
        return self

    # Convenience -------------------------------------------------------------------------

    @property
    def languages(self) -> list[str]:
        return list(self.agent.languages)

    @property
    def client_dir(self) -> Path:
        if self._client_dir is None:
            raise ConfigError("this config was not loaded from a client folder")
        return self._client_dir

    @property
    def scripts_root(self) -> Path:
        return self.client_dir / self.agent.scripts_dir


def _get(data: Any, *keys: str) -> Any:
    """Read a nested value from raw YAML data; None if any level is missing or not a mapping."""
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def find_missing_client_files(data: dict[str, Any], client_dir: Path) -> list[str]:
    """Check every file the config needs, straight from the raw YAML.

    Runs independently of schema validation so that missing files are reported together with
    schema errors in a single pass. Parts of the config that are malformed are skipped here;
    the schema errors already describe them.
    """
    problems: list[str] = []
    languages = _get(data, "agent", "languages")
    if not isinstance(languages, list) or not all(isinstance(x, str) for x in languages):
        languages = []
    scripts_dir = _get(data, "agent", "scripts_dir")
    if not isinstance(scripts_dir, str) or not scripts_dir:
        scripts_dir = "scripts"
    scripts_root = client_dir / scripts_dir

    def check(path: Path, label: str) -> None:
        if not path.is_file():
            problems.append(f"{label} not found")
        elif path.stat().st_size == 0:
            problems.append(f"{label} is empty")

    for lang in languages:
        for rel in REQUIRED_SCRIPT_FILES:
            check(scripts_root / lang / rel, f"{scripts_dir}/{lang}/{rel}")

    # WhatsApp templates: same non-empty set of names in every language.
    template_sets: dict[str, set[str]] = {}
    for lang in languages:
        wa_dir = scripts_root / lang / WHATSAPP_TEMPLATE_DIR
        names = {p.name for p in wa_dir.glob("*.md")} if wa_dir.is_dir() else set()
        template_sets[lang] = names
        if not names:
            problems.append(
                f"{scripts_dir}/{lang}/{WHATSAPP_TEMPLATE_DIR}/ has no templates (*.md)"
            )
        for name in sorted(names):
            check(wa_dir / name, f"{scripts_dir}/{lang}/{WHATSAPP_TEMPLATE_DIR}/{name}")
    all_names: set[str] = set().union(*template_sets.values()) if template_sets else set()
    for lang, names in template_sets.items():
        for name in sorted(all_names - names):
            problems.append(
                f"{scripts_dir}/{lang}/{WHATSAPP_TEMPLATE_DIR}/{name} not found "
                "(every language needs the same WhatsApp templates)"
            )

    for section in ("ai_disclosure", "recording_disclosure"):
        paths = _get(data, "compliance", section)
        if isinstance(paths, dict):
            for lang, rel_path in paths.items():
                if isinstance(rel_path, str):
                    check(client_dir / rel_path, f"compliance.{section}.{lang} ({rel_path})")

    return problems


# --------------------------------------------------------------------------------------------
# Loader


def default_config_path() -> Path:
    env = os.environ.get("ESTARA_CONFIG_PATH")
    if env:
        return Path(env)
    # backend/app/core/config.py -> repo root / client / client.config.yaml
    return Path(__file__).resolve().parents[3] / "client" / "client.config.yaml"


def _format_problems(path: Path, problems: list[str]) -> str:
    lines = [f"Invalid client config: {path}", f"{len(problems)} problem(s) found:"]
    lines.extend(f"  - {p}" for p in problems)
    lines.append("Fix client/client.config.yaml (or the files it points to) and restart.")
    return "\n".join(lines)


def _schema_problems(exc: ValidationError) -> list[str]:
    problems = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        problems.append(f"{loc}: {err['msg'].removeprefix('Value error, ')}")
    return problems


def parse_config(data: Any, client_dir: Path, source: Path | None = None) -> ClientConfig:
    """Validate already-loaded YAML data and the files it refers to.

    Schema errors and missing files are collected together and raised as one ConfigError.
    """
    label = source or client_dir / "client.config.yaml"
    if not isinstance(data, dict):
        raise ConfigError(f"Invalid client config: {label}\n  - the file must be a YAML mapping")
    problems: list[str] = []
    cfg: ClientConfig | None = None
    try:
        cfg = ClientConfig.model_validate(data)
    except ValidationError as exc:
        problems.extend(_schema_problems(exc))
    problems.extend(f"missing file: {p}" for p in find_missing_client_files(data, client_dir))
    if problems or cfg is None:
        raise ConfigError(_format_problems(label, problems))
    cfg._client_dir = client_dir.resolve()
    return cfg


def load_config(path: Path | None = None) -> ClientConfig:
    """Load and validate the client config. Client files resolve relative to its folder."""
    path = path or default_config_path()
    if not path.is_file():
        raise ConfigError(
            f"Client config not found: {path}\n"
            "Set ESTARA_CONFIG_PATH or create client/client.config.yaml."
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid client config: {path}\n  - YAML syntax error: {exc}") from exc
    return parse_config(data, path.parent, source=path)


def main() -> int:
    """``python -m app.core.config`` validates the config and prints the result."""
    import sys

    try:
        cfg = load_config()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if cfg.voice.mode == "native":
        voice_note = "native (model and voice chosen in Vapi)"
    else:
        voice_note = "ready" if cfg.voice.custom_ready else "not set yet; calls go to a human"
    print(
        f"Client config OK: {cfg.company.name}, languages {cfg.agent.languages}, voice {voice_note}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
