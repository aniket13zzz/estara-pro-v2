"""Text harness for the Sales Brain: ``make chat`` (``python -m app.brain.cli``).

Opens a web-chat conversation with a new test customer against the database in DATABASE_URL
(load the sample inventory first with ``make seed-sample``) and talks to the brain line by line.

    python -m app.brain.cli --lang hi            # the configured LLM (needs its key in .env)
    python -m app.brain.cli --lang en --llm offline --debug

``--llm offline`` uses the labelled plumbing stand-in (``app/providers/llm/offline.py``): it
calls the tools in a fixed order and echoes their spoken strings. It shows that the backend
works end to end without a key; it says nothing about how a real model behaves.

Type ``/quit`` (or end input) to stop. Each turn is committed, so the conversation, lead,
matches and any visit request are visible in the database afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from typing import TextIO

from sqlalchemy import func, select

from app.brain.service import SalesBrain
from app.conversations.service import get_or_start
from app.core.config import ClientConfig, ConfigError, load_config
from app.core.enums import Channel, IdentityType, RecordStatus
from app.core.settings import Settings
from app.db.models import Customer, CustomerIdentity, Project
from app.db.session import make_engine, make_sessionmaker
from app.leads.state import open_lead
from app.providers.embeddings import lazy_embedder
from app.providers.llm import LLMClient, LLMNotConfigured, Pacer, RetryPolicy, get_llm
from app.providers.llm.offline import OfflineLLM


def build_llm(choice: str, config: ClientConfig, settings: Settings) -> LLMClient:
    if choice == "offline":
        return OfflineLLM()
    return get_llm(config, settings)


async def chat(
    *,
    language: str,
    llm_choice: str,
    debug: bool,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
) -> int:
    def say(text: str) -> None:
        print(text, file=stdout, flush=True)

    try:
        config = load_config()
    except ConfigError as exc:
        say(str(exc))
        return 1
    if language not in config.agent.languages:
        say(f"--lang must be one of {config.agent.languages}")
        return 1
    settings = Settings()
    try:
        llm = build_llm(llm_choice, config, settings)
    except LLMNotConfigured as exc:
        say(f"{exc}. Add the key to .env, or try --llm offline for a plumbing check.")
        return 2
    engine = make_engine(settings.database_url)
    factory = make_sessionmaker(engine)
    try:
        async with factory() as db:
            projects = await db.scalar(
                select(func.count()).select_from(Project)
                .where(Project.status == RecordStatus.APPROVED)
            )  # fmt: skip
            if not projects:
                say("No approved projects in the database. Run `make seed-sample` first.")
                return 1
            customer = Customer(preferred_language=language, name=None)
            db.add(customer)
            await db.flush()
            session_id = f"cli-{uuid.uuid4().hex[:12]}"
            db.add(
                CustomerIdentity(
                    customer_id=customer.id, identity_type=IdentityType.WEB_SESSION,
                    value=session_id, verified=False,
                )
            )  # fmt: skip
            brain = SalesBrain(
                db, config, llm=llm, retry=RetryPolicy.from_settings(settings),
                pacer=Pacer(settings.llm_min_interval_seconds),
                on_llm_wait=lambda note: say(f"  [{note}]"),
                embedder=lazy_embedder(object(), config, settings),
            )  # fmt: skip
            conversation = await get_or_start(
                db, customer=customer, channel=Channel.WEB_CHAT, config=config,
                now=brain.clock.now(),
            )  # fmt: skip
            await db.commit()
            label = "OFFLINE PLUMBING CHECK, not a language model" if llm_choice == "offline" \
                else f"{llm.provider} / {llm.model}"  # fmt: skip
            say(f"ESTARA chat test | {config.company.name} | language {language} | {label}")
            say("Type /quit to stop.\n")
            while True:
                print("you> ", end="", file=stdout, flush=True)
                line = stdin.readline()
                if not line:
                    say("")
                    break
                text = line.strip()
                if not text:
                    continue
                if text in {"/quit", "/exit"}:
                    break
                if not stdin.isatty():
                    say(text)  # echo piped input so transcripts read naturally
                reply = await brain.respond(conversation, text)
                await db.commit()
                say(f"{config.agent.name}> {reply.text}")
                if debug:
                    tools = [f"{t['name']}({json.dumps(t['arguments'], ensure_ascii=False)})"
                             for t in reply.tool_calls]  # fmt: skip
                    lead = await open_lead(db, customer)
                    lead_state = lead.state.value if lead else None
                    brain_state = {k: reply.meta.get(k) for k in ("guard", "fallback",
                                   "escalation_enforced", "language")}  # fmt: skip
                    say(f"  [lang {reply.language} | lead {lead_state} | tools {tools}]")
                    say(f"  [meta {json.dumps(brain_state, ensure_ascii=False, default=str)}]")
                say("")
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Talk to the ESTARA Sales Brain in a terminal.")
    parser.add_argument("--lang", default=None, help="customer language: en or hi")
    parser.add_argument(
        "--llm", choices=["config", "offline"], default="config",
        help="config: the provider in client.config.yaml; offline: plumbing check",
    )  # fmt: skip
    parser.add_argument("--debug", action="store_true", help="show tools, lead state, guards")
    args = parser.parse_args(argv)
    language = args.lang
    if language is None:
        language = input("Language (en/hi): ").strip() or "en"
    return asyncio.run(chat(language=language, llm_choice=args.llm, debug=args.debug))


if __name__ == "__main__":
    raise SystemExit(main())
