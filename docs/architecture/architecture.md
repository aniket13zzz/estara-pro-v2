# Architecture

ESTARA Pro is a modular monolith (ADR 0002): one FastAPI backend whose modules map to the spec,
an ARQ worker sharing the same code, a Next.js dashboard, and PostgreSQL + Redis. One deployment
per client (ADR 0001).

```
                 Caller ──► Indian number ──forward/SIP──► Vapi (STT, turn-taking, Cartesia TTS)
                                                            │ assistant-request, tool-calls,
                                                            │ end-of-call-report (signed)
 Website widget ─┐                                          ▼
 WhatsApp Cloud ─┼─ webhooks / public chat API ──► ┌───────────────────────────────┐
 Email (IMAP/OAuth)┘                               │ FastAPI backend (app/)         │
                                                   │  channels → brain → truth      │
 Dashboard (Next.js) ── /api (same origin) ──────► │  leads, visits, handoff, usage │
                                                   └──────┬─────────────┬──────────┘
                                                          │             │ enqueue
                                                   PostgreSQL 16     Redis ◄── ARQ worker
                                                   + pgvector           (follow-ups, staleness,
                                                                         imports, retention)
```

## Modules (`backend/app/`)

| Module | Responsibility | Built in |
| --- | --- | --- |
| `core` | Client config schema and loader, settings, logging with redaction, enums, health | 1 |
| `db` | SQLAlchemy models, async session factory, Alembic migrations | 1 |
| `auth` | Users, argon2 passwords, sessions, CSRF, login rate limit and lockout, roles | 1 |
| `audit` | Append-only audit log helper | 1 |
| `datacenter` | Upload → scan → parse → validate → preview → approve → import; knowledge store; properties overview | 2 |
| `truth` | Approved-facts service, staleness, `unavailable` results, en/hi formatters | 2 |
| `customers`, `conversations` | Identity resolution, memory, message storage with language | 3 |
| `brain` | Sales Brain prompts, extraction, objections, escalation; `brain.respond()` | 3 |
| `matching` | Structured search and explainable ranking | 3 |
| `leads` | State machine and deterministic scoring | 3 |
| `channels` | Vapi, web chat, WhatsApp, email entry points | 4, 6 |
| `providers` | Adapters: llm, voice, whatsapp, email, embeddings (embeddings built in Stage 2) | 2–6 |
| `visits`, `handoff` | Visit requests, handoff records with context, follow-up rows (Stage 3); staff confirmation, notifications, follow-up sending (Stage 5) | 3, 5 |
| `usage` | Cost tracking, monthly limits, kill switch | 7 |
| `jobs` | ARQ worker and background jobs | 1 (worker), 2+ |

Rules that shape the code:

- **Config is the only per-client switch.** `ClientConfig` is loaded once at startup and placed
  on `app.state.client_config`. If a client need would require a code change, it becomes a
  config field instead.
- **Languages come from `agent.languages`.** No code lists languages.
- **Channel-agnostic brain.** Every channel calls `brain.respond(conversation, message)`.
- **Truth Layer boundary.** Only `app/truth` reads commercial tables for customer-facing answers.
  pgvector is used only by knowledge retrieval.
- **Adapters.** Business logic depends on interfaces in `app/providers/*`; vendor SDKs stay
  inside adapters.

## Data Center and Truth Layer (Stage 2)

```
upload ─► scan (type from bytes, size, zip/macro checks) ─► parse (CSV/XLSX) ─► validate
   │                                                                      │ no errors
   └─► PDF/DOCX ─► extract text ─► chunk ─► embed (adapter) ─► draft document   ▼
                                                               draft versions (source_batch_id)
admin approves ─► drafts approved, replaced versions archived ─► Truth Layer serves them
```

- `app/datacenter/entities.py` defines each data type once (columns, keys, references); it
  drives templates, parsing, validation and import.
- `app/truth/service.py` answers per fact type (price, availability, offer, payment plan,
  possession, area, amenity) with a `TruthFact` (value, display, spoken per
  configured language, sources, approved_at) or `Unavailable` (`missing`, `stale`,
  `unapproved`, `contradictory`). `app/truth/staleness.py` is the single staleness rule.
- `app/truth/formatters.py` produces every spoken number, amount, date and area.
- The ARQ worker runs `check_staleness` daily at 06:00 client time (and at start-up); it opens or
  resolves the `stale_data` dashboard alert.

## Sales Brain (Stage 3)

```
channel ─► identity.resolve ─► conversations.get_or_start ─► SalesBrain.respond(conversation, text)
  1 record inbound (language detected, switch logged)
  2 kill switch / monthly LLM budget? ─► paused_handoff script + handoff
  3 rules: extraction (budget, BHK, timeline, loan, purpose, localities) ─► lead requirements,
    ADR 0007 system transitions; signals (objection, escalation, "are you a bot?")
  4 prompt (one template + language block + backend notes) ─► LLM ⇄ tools (≤ 6 rounds)
       tools: Truth Layer, matching, scoring, slots, visit request, handoff, follow-up
  5 guards: grounding (numbers/dates only from tools, customer or scripts), reply language
       ─► one rewrite ─► remove ungrounded sentences ─► if empty: script + handoff
  6 enforce escalation (person / legal / repeated anger ─► handoff); AI disclosure
  7 record outbound with tool calls, guard results; usage_records per LLM call
```

- `app/brain/quantities.py` is the one reader of numbers in en/hi/Hinglish. Extraction and
  the grounding guard both use it, so they agree on what "डेढ़ करोड़" or "85L" means.
- Tool handlers (`app/brain/tools.py`) are the only way the model changes data, and they
  change it through `leads`, `visits`, `handoff` and `matching` rules.
- `make chat` is a terminal harness for the same `respond()`; `make eval` runs 30 scripted
  conversations through it.

## Request flow (dashboard)

The dashboard calls `/api/*` on its own origin; Next.js rewrites these to the backend, so the
session cookie is first-party. Protected routes depend on `current_user` (session cookie +
CSRF header for state-changing methods) or `require_role(...)`. A route-guard test fails if
any `/api` route outside the public list lacks this dependency.

## Startup

`uvicorn app.asgi:app` → `build_app_or_exit()`:

1. Reads environment settings; exits 1 with a readable message if, for example,
   `SESSION_SECRET` is missing.
2. Loads `client/client.config.yaml`, validates the schema and every referenced file, and
   exits 1 listing all problems if anything is wrong.
3. Creates the database engine and Redis client in the lifespan.

The ARQ worker (`arq app.jobs.worker.WorkerSettings`) also validates the config on startup.

## Repository layout

```
client/            config, scripts/en|hi (all customer-facing text), branding — client-owned
backend/app/       modules above; backend/alembic/ migrations; backend/tests/
dashboard/         Next.js App Router, TypeScript strict; e2e/ Playwright
widget/            embeddable chat script (Stage 6)
docs/              spec support docs, ADRs (docs/decisions), roadmap, progress
docker-compose.yml postgres+pgvector, redis, backend, worker, dashboard
```
