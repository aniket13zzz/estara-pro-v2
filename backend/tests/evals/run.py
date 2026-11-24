"""Conversation eval runner: ``make eval`` (``python -m tests.evals.run``).

Runs every scripted conversation in ``tests/evals/conversations/`` through the real Sales
Brain with the LLM from ``client.config.yaml`` (key from .env), against a scratch database
loaded with the fake sample inventory, then applies ``tests/evals/checks.py``.

    python -m tests.evals.run                         # all 30, live LLM
    python -m tests.evals.run --style hi              # one language: en | hi | hinglish
    python -m tests.evals.run --id en_01_new_town_visit   # one conversation (repeatable)
    python -m tests.evals.run --list                  # ids and styles, nothing run
    python -m tests.evals.run --smoke                 # 3 core conversations (en, hi, hinglish)
    python -m tests.evals.run --llm offline           # plumbing smoke test, NOT an eval result

Pacing for free tiers (see ``--help``): at least ``--llm-delay`` seconds between LLM calls
(default 10, or EVAL_LLM_DELAY_SECONDS). A 429 is retried up to ``--rate-limit-retries``
times, waiting what the provider asks for (``Retry-After`` / Google ``RetryInfo``) or
backing off, as long as a single wait is at most ``--max-wait`` seconds. A conversation whose
LLM call still gets a 429 is reported as "rate limited, not run" and is not scored as a Sales
Brain failure. A response slower than ``--llm-timeout`` (default 120 s) is retried once; if
it is still slow, the conversation is "timed out, not run". An HTTP 5xx (provider outage or
"high demand") is retried 3 times over about a minute (10/20/40 s); if it persists, the
conversation is "provider unavailable, not run". A used-up daily quota, or three of these
provider problems in a row, stops the run; the rest are "not run". The report counts every
LLM request sent, so you can see what a run costs against your quota.

The database in EVAL_DATABASE_URL (default: TEST_DATABASE_URL) is migrated and WIPED first.
Never point it at a database with real data.

Results go to ``tests/evals/results/<UTC time>-<provider>.json`` and ``.md`` (not committed;
copy the summary into docs/progress.md when recording a run). Exit code: 0 every selected
conversation ran and passed; 1 at least one failed; 3 none failed but some were rate limited,
timed out or not run; 2 not runnable (no key, no database, nothing selected).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import create_user
from app.brain.scripts import Scripts
from app.brain.service import SalesBrain
from app.conversations.service import get_or_start
from app.core.config import ClientConfig, load_config
from app.core.enums import Channel, IdentityType, Role
from app.core.settings import Settings
from app.datacenter import service as datacenter
from app.db import models  # noqa: F401  (registers every table)
from app.db.base import Base
from app.db.models import Customer, CustomerIdentity, Handoff, Lead
from app.db.session import make_engine, make_sessionmaker
from app.providers.llm import LLMClient, LLMNotConfigured, Pacer, RetryPolicy, get_llm
from app.providers.llm.offline import OfflineLLM
from app.seed import read_inventory
from app.truth.staleness import SystemClock
from tests.evals.checks import (
    LATE_STATES,
    CheckResult,
    EvalTurn,
    Expectation,
    guard_interventions,
    run_checks,
    visit_requested,
)

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
CONVERSATIONS = HERE / "conversations"
RESULTS = HERE / "results"
INVENTORY = BACKEND / "tests" / "fixtures" / "sample_inventory"
EVAL_PASSWORD = "eval-only-password-not-secret"
STYLES = ("en", "hi", "hinglish")
DEFAULT_LLM_DELAY = 10.0  # seconds between LLM calls; free-tier limits are per minute
CONSECUTIVE_LIMITED_STOP = 3
# The smoke eval: one core visit-booking conversation per language (fits a free tier's day).
SMOKE_IDS = ("en_01_new_town_visit", "hi_01_new_town_visit", "hg_01_new_town_visit")
# A different, fake customer with a booked visit, seeded before every run. No reply may ever
# mention them: the agent must only know the customer it is talking to.
DECOY_NAME = "Rohan Mehta"
DECOY_PHONE = "+919000000099"
DECOY_EMAIL = "rohan.mehta@example.com"

PASSED, FAILED, RATE_LIMITED, NOT_RUN = "passed", "failed", "rate_limited", "not_run"
TIMED_OUT = "timed_out"  # the provider did not answer within --llm-timeout, after a retry
UNAVAILABLE = "provider_unavailable"  # HTTP 5xx (outage or "high demand") after retries
PROVIDER_PROBLEMS = {RATE_LIMITED, TIMED_OUT, UNAVAILABLE}
INCOMPLETE = PROVIDER_PROBLEMS | {NOT_RUN}
DEFAULT_LLM_TIMEOUT = 120.0
REQUEST_KEYS = ("requests", "succeeded", "rate_limited", "timeouts", "unavailable")


@dataclass
class Conversation:
    id: str
    style: str  # en | hi | hinglish
    reply_language: str
    description: str
    turns: list[tuple[str, str]]  # (customer text, expected reply language)
    expect: Expectation


@dataclass
class Outcome:
    id: str
    style: str
    description: str
    status: str  # passed | failed | rate_limited | timed_out | not_run
    checks: list[CheckResult] = field(default_factory=list)
    guard: dict[str, int] = field(default_factory=dict)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0
    note: str = ""
    # LLM requests sent for this conversation, including retries, and customer turns run.
    llm: dict[str, int] = field(default_factory=lambda: dict.fromkeys(REQUEST_KEYS, 0))
    turns_run: int = 0
    qualified: bool = False  # the lead reached qualified (or later) during the conversation
    visit_requested: bool = False  # the agent requested a site visit (the goal)

    @property
    def passed(self) -> bool:
        return self.status == PASSED


def load_conversations(
    only: str | None = None, *, ids: list[str] | None = None, styles: list[str] | None = None
) -> list[Conversation]:
    out: list[Conversation] = []
    for path in sorted(CONVERSATIONS.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if only and not data["id"].startswith(only):
            continue
        if ids and data["id"] not in ids:
            continue
        if styles and data["style"] not in styles:
            continue
        default = data["reply_language"]
        turns: list[tuple[str, str]] = []
        for item in data["turns"]:
            if isinstance(item, str):
                turns.append((item, default))
            else:
                turns.append((item["text"], item.get("reply_language", default)))
        expect = Expectation(**(data.get("expect") or {}))
        out.append(Conversation(data["id"], data["style"], default, data["description"].strip(),
                                turns, expect))  # fmt: skip
    return out


def migrate(url: str) -> None:
    env = {**os.environ, "DATABASE_URL": url}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND, env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if result.returncode != 0:
        raise SystemExit(f"alembic upgrade failed:\n{result.stderr}")


async def prepare(db: AsyncSession, config: ClientConfig, settings: Settings) -> None:
    tables = ", ".join(sorted(Base.metadata.tables))
    await db.execute(text(f"TRUNCATE {tables} CASCADE"))
    await db.commit()
    admin = await create_user(db, email="eval-admin@example.com", name="Eval Admin",
                              password=EVAL_PASSWORD, role=Role.CLIENT_ADMIN)  # fmt: skip
    await create_user(db, email=config.sales_team[0].email, name=config.sales_team[0].name,
                      password=EVAL_PASSWORD, role=Role.SALES_STAFF)  # fmt: skip
    await db.commit()
    now = datetime.now(UTC)
    for entity, filename, content in read_inventory(INVENTORY):
        batch = await datacenter.create_batch(
            db, settings=settings, config=config, filename=filename, data=content,
            meta=datacenter.UploadMeta(entity=entity), user_id=admin.id,
        )  # fmt: skip
        await datacenter.approve(db, batch.id, user_id=admin.id, now=now)
    decoy = Customer(name=DECOY_NAME, preferred_language="en",
                     memory={"note": "booked a visit, VIP buyer"})  # fmt: skip
    db.add(decoy)
    await db.flush()
    for kind, value in ((IdentityType.PHONE, DECOY_PHONE), (IdentityType.EMAIL, DECOY_EMAIL)):
        db.add(CustomerIdentity(customer_id=decoy.id, identity_type=kind, value=value,
                                verified=True, verified_at=now))  # fmt: skip
    db.add(Lead(customer_id=decoy.id, source_channel=Channel.VOICE))
    await db.commit()


def private_values(config: ClientConfig) -> list[str]:
    """Everything a reply must never contain (see checks.check_no_leak)."""
    out = [DECOY_NAME, DECOY_PHONE, DECOY_EMAIL, "eval-admin@example.com",
           config.escalation.human_transfer_number]  # fmt: skip
    for person in config.sales_team:
        out += [person.phone, person.email]
    return [str(v) for v in out if v]


@dataclass
class Runner:
    config: ClientConfig
    llm: LLMClient
    scripts: Scripts
    retry: RetryPolicy
    pacer: Pacer
    say: Any = print

    async def run_one(self, db: AsyncSession, conv: Conversation) -> Outcome:
        started = time.perf_counter()
        customer = Customer()
        db.add(customer)
        await db.flush()
        brain = SalesBrain(
            db, self.config, llm=self.llm, clock=SystemClock(), retry=self.retry,
            pacer=self.pacer, on_llm_wait=lambda note: self.say(f"    [{conv.id}: {note}]"),
        )  # fmt: skip
        conversation = await get_or_start(db, customer=customer, channel=Channel.WEB_CHAT,
                                          config=self.config, now=brain.clock.now())  # fmt: skip
        await db.commit()
        turns: list[EvalTurn] = []
        requests = dict.fromkeys(REQUEST_KEYS, 0)
        for number, (customer_text, expected) in enumerate(conv.turns, start=1):
            reply = await brain.respond(conversation, customer_text)
            await db.commit()
            for key in REQUEST_KEYS:
                requests[key] += int((reply.meta.get("llm") or {}).get(key, 0))
            error = reply.meta.get("llm_error")
            if error in {"rate_limited", "rate_limited_daily", "timeout", "provider_unavailable"}:
                detail = (reply.meta.get("fallback_detail") or "").strip()
                if error == "provider_unavailable":
                    status = UNAVAILABLE
                    note = f"turn {number}: provider unavailable (HTTP 5xx) after retries"
                elif error == "timeout":
                    status = TIMED_OUT
                    note = f"turn {number}: no answer within the LLM timeout, after a retry"
                elif error == "rate_limited_daily":
                    status = RATE_LIMITED
                    note = f"turn {number}: provider daily quota used up (HTTP 429, not retried)"
                else:
                    status = RATE_LIMITED
                    note = f"turn {number}: provider rate limit (HTTP 429), still after retries"
                note += f"; {detail}" if detail else ""
                return Outcome(conv.id, conv.style, conv.description, status,
                               seconds=time.perf_counter() - started, note=note, llm=requests,
                               turns_run=number - 1)  # fmt: skip
            lead = await db.scalar(select(Lead).where(Lead.customer_id == customer.id)
                                   .order_by(Lead.created_at.desc()))  # fmt: skip
            turns.append(EvalTurn(
                customer=customer_text, expected_language=expected, reply=reply.text,
                reply_language=reply.language, tool_calls=reply.tool_calls, meta=reply.meta,
                lead_state=lead.state.value if lead else None,
            ))  # fmt: skip
        reasons = list(
            (await db.execute(select(Handoff.reason).join(Lead, Lead.id == Handoff.lead_id)
                              .where(Lead.customer_id == customer.id))).scalars()
        )  # fmt: skip
        script_texts = [t for lang in self.config.agent.languages
                        for t in self.scripts.all_texts(lang)]  # fmt: skip
        checks = run_checks(turns, conv.expect, scripts=script_texts, handoffs=reasons,
                            private=private_values(self.config))  # fmt: skip
        transcript = [
            {"customer": t.customer, "agent": t.reply, "language": t.reply_language,
             "lead_state": t.lead_state, "tools": [c["name"] for c in t.tool_calls],
             "guard": t.meta.get("guard"), "fallback": t.meta.get("fallback"),
             "llm": t.meta.get("llm")}
            for t in turns
        ]  # fmt: skip
        status = PASSED if all(c.passed for c in checks) else FAILED
        return Outcome(
            conv.id,
            conv.style,
            conv.description,
            status,
            checks,
            guard_interventions(turns),
            transcript,
            time.perf_counter() - started,
            llm=requests,
            turns_run=len(turns),
            qualified=any(t.lead_state in LATE_STATES for t in turns),
            visit_requested=visit_requested(turns),
        )


async def run_all(runner: Runner, db: AsyncSession, conversations: list[Conversation]
                  ) -> list[Outcome]:  # fmt: skip
    outcomes: list[Outcome] = []
    stop_reason = ""
    in_a_row = 0
    for conv in conversations:
        if stop_reason:
            outcomes.append(Outcome(conv.id, conv.style, conv.description, NOT_RUN,
                                    note=stop_reason))  # fmt: skip
            continue
        outcome = await runner.run_one(db, conv)
        outcomes.append(outcome)
        failed = [c.name for c in outcome.checks if not c.passed]
        label = {PASSED: "pass", FAILED: "FAIL", RATE_LIMITED: "LIMIT",
                 TIMED_OUT: "SLOW", UNAVAILABLE: "DOWN"}[outcome.status]  # fmt: skip
        extra = outcome.note if outcome.status in INCOMPLETE else (failed or "")
        sent = outcome.llm["requests"]
        runner.say(f"{conv.id:<28} {label:<5} {outcome.seconds:6.1f}s {sent:3d} req {extra}")
        in_a_row = in_a_row + 1 if outcome.status in PROVIDER_PROBLEMS else 0
        if "daily quota" in outcome.note:
            stop_reason = (
                "stopped: the provider's daily quota is used up (it resets at "
                "midnight Pacific time for Gemini); run the rest later"
            )
        elif in_a_row >= CONSECUTIVE_LIMITED_STOP:
            stop_reason = (
                f"stopped after {in_a_row} conversations in a row could not run (rate limit, "
                "timeout or provider unavailable); run the rest later"
            )
        if stop_reason:
            runner.say(f"  {stop_reason}")
    return outcomes


def write_report(outcomes: list[Outcome], label: str, offline: bool, settings_note: str
                 ) -> tuple[Path, Path]:  # fmt: skip
    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    name = f"{stamp}-{label.replace('/', '_')}"
    # Not Path.with_suffix: it would treat ".8-flash" in "gemini-3.8-flash" as a suffix.
    json_path, md_path = RESULTS / f"{name}.json", RESULTS / f"{name}.md"
    data = [{**asdict(o), "passed": o.passed} for o in outcomes]
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(summary(outcomes, label, offline, settings_note), encoding="utf-8")
    return json_path, md_path


def summary(outcomes: list[Outcome], label: str, offline: bool, settings_note: str = "") -> str:
    ran = [o for o in outcomes if o.status in {PASSED, FAILED}]
    limited = [o for o in outcomes if o.status == RATE_LIMITED]
    slow = [o for o in outcomes if o.status == TIMED_OUT]
    not_run = [o for o in outcomes if o.status == NOT_RUN]
    down = [o for o in outcomes if o.status == UNAVAILABLE]
    sent = {k: sum(o.llm.get(k, 0) for o in outcomes) for k in REQUEST_KEYS}
    turns_done = sum(o.turns_run for o in ran)
    per_turn = sum(o.llm.get("requests", 0) for o in ran) / turns_done if turns_done else 0
    passed = sum(o.passed for o in ran)
    lines = [f"# Conversation eval: {label}", ""]
    if offline:
        lines += ["**OFFLINE PLUMBING CHECK. Not an eval result: no language model was used.**", ""]
    lines += [
        f"Run at {datetime.now(UTC).isoformat(timespec='seconds')}. {settings_note}",
        "",
        f"Selected {len(outcomes)}. Ran {len(ran)}: **passed {passed}, failed "
        f"{len(ran) - passed}**. Rate limited, not run: {len(limited)}. Timed out, not "
        f"run: {len(slow)}. Provider unavailable, not run: {len(down)}. Not run: "
        f"{len(not_run)}. Only conversations that ran are scored.",
        "",
        f"LLM requests sent: {sent['requests']} ({sent['succeeded']} answered, "
        f"{sent['rate_limited']} rate limited, {sent['timeouts']} timed out, "
        f"{sent['unavailable']} provider unavailable). Conversations "
        f"that ran used {per_turn:.1f} requests per customer turn over {turns_done} turns.",
        "",
    ]  # fmt: skip
    qualified = [o for o in ran if o.qualified]
    booked = sum(o.visit_requested for o in qualified)
    lines += [f"Visit booked on qualified conversations: **{booked} of {len(qualified)}**.", ""]
    names = ["numbers_grounded", "language", "no_ai_failure", "no_leak", "visit_offered",
             "unavailable_handled", "handoff"]  # fmt: skip
    lines.append("| Check | Passed | Applies to |")
    lines.append("| --- | --- | --- |")
    for name in names:
        applicable = [c for o in ran for c in o.checks if c.name == name]
        lines.append(f"| {name} | {sum(c.passed for c in applicable)} | {len(applicable)} |")
    totals = {k: sum(o.guard.get(k, 0) for o in ran) for k in (ran[0].guard if ran else {})}
    lines += ["", "Guard interventions on the model's first drafts (the final replies are what "
              f"the checks score): {totals}", ""]  # fmt: skip
    lines.append("| Conversation | Style | Result | LLM requests | Details |")
    lines.append("| --- | --- | --- | --- | --- |")
    words = {PASSED: "pass", FAILED: "FAIL", RATE_LIMITED: "rate limited, not run",
             TIMED_OUT: "timed out, not run", UNAVAILABLE: "provider unavailable, not run",
             NOT_RUN: "not run"}  # fmt: skip
    for o in outcomes:
        details = "; ".join(f"{c.name}: {c.detail}" for c in o.checks if not c.passed) or o.note
        lines.append(f"| {o.id} | {o.style} | {words[o.status]} | {o.llm.get('requests', 0)} "
                     f"| {details} |")  # fmt: skip
    return "\n".join(lines) + "\n"


def exit_code(outcomes: list[Outcome]) -> int:
    if any(o.status == FAILED for o in outcomes):
        return 1
    if any(o.status in INCOMPLETE for o in outcomes):
        return 3
    return 0


def _split(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    return [v.strip() for item in values for v in item.split(",") if v.strip()]


async def main_async(args: argparse.Namespace) -> int:
    ids, styles = _split(args.id), _split(args.style)
    if args.smoke:
        ids = list(SMOKE_IDS) if not ids else ids
    bad = sorted(set(styles or []) - set(STYLES))
    if bad:
        print(f"Unknown --style {bad}; use {', '.join(STYLES)}.")
        return 2
    conversations = load_conversations(args.only, ids=ids, styles=styles)
    if ids:
        missing = sorted(set(ids) - {c.id for c in conversations})
        if missing:
            print(f"Unknown --id {missing}. `--list` shows the ids.")
            return 2
    if args.list:
        for c in load_conversations():
            print(f"{c.id:<28} {c.style:<9} {len(c.turns)} turns  {c.description}")
        return 0
    if not conversations:
        print("No conversations matched.")
        return 2
    config = load_config()
    settings = Settings()
    settings = settings.model_copy(update={"llm_timeout_seconds": args.llm_timeout})
    url = os.environ.get("EVAL_DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    if not url:
        print("Set EVAL_DATABASE_URL or TEST_DATABASE_URL (a scratch database; it is wiped).")
        return 2
    offline = args.llm == "offline"
    try:
        llm: LLMClient = OfflineLLM() if offline else get_llm(config, settings)
    except LLMNotConfigured as exc:
        print(f"Cannot run the eval: {exc}. Add the key to .env and run `make eval` again.")
        return 2
    delay = 0.0 if offline else args.llm_delay
    retry = RetryPolicy(
        # 5xx "high demand" usually passes within a minute: waits of about 10, 20 and 40 s.
        max_attempts=4, base_delay=10.0, rate_limit_attempts=args.rate_limit_retries + 1,
        rate_limit_base_delay=5.0, max_rate_limit_wait=args.max_wait,
        timeout_attempts=settings.llm_timeout_attempts,
    )  # fmt: skip
    settings_note = (
        f"Model {llm.provider}/{llm.model}, temperature {config.llm.temperature:g}, thinking "
        f"{config.llm.thinking_level or 'model default'}; {delay:g}s between LLM calls; "
        f"429 retries {args.rate_limit_retries}, max wait {args.max_wait:g}s; "
        f"response timeout {args.llm_timeout:g}s (one retry); 5xx retries 3 (10/20/40s)."
    )
    print(f"{len(conversations)} conversation(s). {settings_note}", flush=True)
    migrate(url)
    engine = make_engine(url)
    factory = make_sessionmaker(engine)
    label = "offline-plumbing" if offline else f"{llm.provider}-{llm.model}"
    runner = Runner(config, llm, Scripts(config), retry, Pacer(delay),
                    say=lambda m: print(m, flush=True))  # fmt: skip
    try:
        async with factory() as db:
            await prepare(db, config, settings)
            outcomes = await run_all(runner, db, conversations)
    finally:
        await engine.dispose()
    json_path, md_path = write_report(outcomes, label, offline, settings_note)
    print()
    print(summary(outcomes, label, offline, settings_note))
    print(f"Details: {json_path}\nSummary: {md_path}")
    return exit_code(outcomes)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the ESTARA conversation evals.")
    parser.add_argument("--llm", choices=["config", "offline"], default="config")
    parser.add_argument("--id", action="append", help="exact conversation id (repeatable)")
    parser.add_argument("--style", action="append", help="en, hi or hinglish (repeatable)")
    parser.add_argument("--only", default=None, help="ids starting with this prefix")
    parser.add_argument("--list", action="store_true", help="list conversations and exit")
    parser.add_argument(
        "--smoke", action="store_true",
        help="the 3 core conversations (one per language); add --id to pick one",
    )  # fmt: skip
    parser.add_argument(
        "--llm-delay", type=float,
        default=_env_float("EVAL_LLM_DELAY_SECONDS", DEFAULT_LLM_DELAY),
        help="minimum seconds between LLM calls (default 10, or EVAL_LLM_DELAY_SECONDS). "
        "Free-tier limits are per minute and shown in Google AI Studio; 10 s keeps a run at "
        "6 requests a minute",
    )  # fmt: skip
    parser.add_argument("--rate-limit-retries", type=int, default=6,
                        help="retries after an HTTP 429 (default 6)")  # fmt: skip
    parser.add_argument(
        "--max-wait",
        type=float,
        default=90.0,
        help="longest single wait for a 429, in seconds (default 90)",
    )
    parser.add_argument(
        "--llm-timeout", type=float,
        default=_env_float("EVAL_LLM_TIMEOUT_SECONDS", DEFAULT_LLM_TIMEOUT),
        help="seconds to wait for one LLM response, thinking included (default 120, or "
        "EVAL_LLM_TIMEOUT_SECONDS); a timed-out request is retried once",
    )  # fmt: skip
    return asyncio.run(main_async(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
