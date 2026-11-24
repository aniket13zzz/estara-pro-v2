# ESTARA Pro — Single-Build Implementation Plan

## Build strategy

Build Phases 1–20 as one continuous build, in dependency order, with English and Hindi designed in from the first commit. Phases 21–22 (pilot and production) need a real client and real time, so they follow the build rather than run inside it.

"All at once" works if the coding agent follows three rules:

- **One codebase, one pass, ordered by dependency.** Every module gets built, but foundations (config, database, auth, Truth Layer) land before anything that depends on them. Nothing is stubbed and forgotten.
- **Vertical slice first, inside the single build.** The first working path is: upload sample data → call the agent in Hindi or English → lead, summary, matched property and visit request appear on the dashboard. Every other module is then added onto a working spine, not a pile of untested parts.
- **Language is a property of every layer, not a later feature.** Config, scripts, Truth Layer formatting, prompts, templates, test data and dashboard all carry `en` and `hi` from day one. Retrofitting Hindi later costs more than building it in.

What a single build cannot do on its own:

- Verify a real Indian phone number, WhatsApp Business approval, or email OAuth without your accounts and credentials.
- Prove call quality in Hindi without real test calls by real speakers.
- Pilot with a client. That needs weeks of real traffic.

Those items are built and tested against labelled mocks, then marked **externally blocked** until you supply what's listed in *External dependencies*.

## Bilingual design (English + Hindi)

Treat the language as a per-conversation state that the backend owns, with Hinglish (mixed Hindi-English) as the expected normal case, not an edge case.

**Language handling**

- Supported languages come from `languages: [en, hi]` in config. Code never hardcodes a language list.
- Each call starts with a bilingual greeting from config, e.g. English line then a short Hindi line, or the client's chosen default.
- The detected language is stored on the conversation and on the customer profile (`preferred_language`). Returning callers are greeted in their last language.
- Switching mid-call is allowed. The LLM replies in the caller's current language. The backend records each switch.
- Hindi responses default to natural spoken Hinglish in Devanagari script for TTS, with property names, project names and "BHK" kept in English. Avoid formal, textbook Hindi; buyers don't speak it.

**Truth Layer formatting (critical)**

Prices and dates are never written by the LLM from raw numbers. The backend returns pre-formatted, speakable strings per language:

| Fact | Stored value | English spoken | Hindi spoken |
| --- | --- | --- | --- |
| Price | 8500000 (INR) | 85 lakh rupees | पचासी लाख रुपये |
| Price range | 12000000–15000000 | 1.2 to 1.5 crore | एक करोड़ बीस लाख से डेढ़ करोड़ |
| Possession | 2027-12-31 | December 2027 | दिसंबर 2027 |
| Area | 1250 sq ft | 1,250 square feet | बारह सौ पचास स्क्वायर फ़ीट |

A shared `formatters` module (lakh/crore, Indian digit grouping, number-to-Hindi-words, dates) produces these, with unit tests for every rule. This also prevents the voice engine mispronouncing "85,00,000".

**Per-language content**

- Greeting, AI disclosure, recording disclosure, objection handling, sales script, follow-up messages and WhatsApp templates exist in both `en` and `hi` files.
- A missing translation fails config validation at startup. It never silently falls back.
- Approved property data stays in one language (usually English) in the database. The agent describes it in the caller's language; facts and numbers come only from the Truth Layer.

**Dashboard**

The dashboard UI stays in English for sales staff. Transcripts are shown as spoken (Devanagari or mixed), with an English summary generated per call so staff can scan quickly.

## Voice stack on Vapi

Vapi runs the call (telephony, speech-to-text, turn-taking, text-to-speech); the ESTARA backend owns the assistant definition, the tools and all commercial truth.

| Layer | Choice | Notes |
| --- | --- | --- |
| Inbound routing | Client number → call forwarding (start) or SIP trunk → Vapi number | Porting documented as a later option |
| Assistant config | Returned per call by the backend via Vapi's `assistant-request` server event | Lets the backend inject greeting, language, returning-caller context and the kill switch per call. Nothing is hand-edited in the Vapi dashboard |
| Speech-to-text | A transcriber with Hindi + English code-switching | Benchmark 2–3 candidates (e.g. Deepgram multilingual mode, Azure, Gladia) on recorded Hinglish property calls before locking one. Pick on word accuracy for numbers and place names |
| LLM | Gemini via Vapi's model config, with ESTARA tools | Provider set from `llm_provider`. Swapping to Claude/OpenAI = config change in the generated assistant |
| Text-to-speech | Cartesia Sonic multilingual (Sonic 3.5) | One voice that speaks both English and Hindi naturally, so a mid-call switch doesn't change the voice. Confirm the exact model ID and voice ID in the Vapi dashboard and store them in config |
| Tools | `POST /api/v1/vapi/tools`, signed | Contract in *Vapi tool contract* |
| End of call | `end-of-call-report` webhook | Stores recording link, transcript, summary, duration, outcome, cost |

**Latency budget** (callers notice anything over ~1.5 s of silence):

- Each tool call responds in under 800 ms at p95. Property search and Truth Layer lookups hit indexed Postgres, never the LLM.
- Knowledge retrieval (pgvector) capped at 3 chunks, under 400 ms.
- Filler lines in both languages ("एक सेकंड, मैं चेक करता हूँ" / "One moment, let me check") from config, used when a tool may be slow.
- Measure real end-to-end turn latency from call logs, not guesses.

**Fallbacks**

- Caller asks for a person, or the agent can't help → `transfer_to_human` to the configured number, with a one-line whisper summary where the transfer method supports it.
- Backend unreachable, LLM error, or kill switch on → the assistant-request returns a minimal config that plays the disclosure and transfers immediately. If the backend is fully down, Vapi's configured fallback destination forwards the call to the human number.
- Outside working hours with no human available → take requirements, create the lead, promise a callback.

## Repository structure

One repo, a modular monolith: a FastAPI backend whose modules map one-to-one to the spec, a Next.js dashboard, and a `client/` folder that is the only thing that differs between clients.

```
estara-pro/
├── client/                      # ONLY folder that changes per client
│   ├── client.config.yaml
│   ├── scripts/en/  scripts/hi/ # ALL customer-facing text (see "Script files" below),
│   │                            #   including whatsapp/ templates
│   └── branding/                # logo, colors
├── backend/
│   ├── app/
│   │   ├── core/        # config loader + validation, settings, logging, security, i18n
│   │   ├── db/          # models, session, alembic migrations
│   │   ├── auth/        # users, roles, sessions
│   │   ├── datacenter/  # upload, parse, validate, preview, approve, import
│   │   ├── truth/       # approved facts API + formatters (lakh/crore, Hindi numbers)
│   │   ├── customers/   # identity resolution, profiles, memory
│   │   ├── conversations/
│   │   ├── brain/       # Sales Brain: intent, extraction, prompts, objection logic
│   │   ├── matching/    # structured search + explainable ranking
│   │   ├── leads/       # lifecycle state machine, scoring rules
│   │   ├── visits/      # site visits + confirmation
│   │   ├── handoff/     # human handoff, follow-ups
│   │   ├── channels/    # vapi/, web/, whatsapp/, email/
│   │   ├── providers/   # adapters: llm/, voice/, whatsapp/, email/, embeddings/
│   │   ├── usage/       # cost tracking, limits, kill switch
│   │   ├── audit/
│   │   └── jobs/        # background jobs (follow-ups, staleness, imports)
│   └── tests/           # unit/, integration/, contract/, e2e/, fixtures/ (en + hi)
├── dashboard/           # Next.js
├── widget/              # embeddable chat script
├── docs/                # spec, architecture, ADRs, clone guide
├── docker-compose.yml   # postgres+pgvector, redis, backend, worker, dashboard
├── setup_client.sh
└── .github/workflows/   # lint, typecheck, tests, build
```

Stack: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, PostgreSQL 16 + pgvector, Redis with a lightweight job runner (ARQ), Next.js on Vercel, backend on Render/Railway/Fly/VPS. Ruff, mypy, pytest, ESLint, TypeScript strict, Playwright for dashboard e2e.

## client.config.yaml design

The config is validated by a Pydantic schema at startup and by `setup_client.sh`; an invalid or incomplete config (including a missing Hindi text) stops the app with a clear error.

```yaml
template_version: "1.0.0"
tier: pro

company:
  name: "Sunrise Developers"
  timezone: "Asia/Kolkata"
  currency: INR
  branding: { logo: branding/logo.png, primary_color: "#0F4C81" }

agent:
  name: "Priya"
  tone: warm_professional
  languages: [en, hi]
  default_language: hi
  greeting_mode: bilingual          # bilingual | default_language
  scripts_dir: scripts              # scripts/en/*.md, scripts/hi/*.md

voice:
  provider: vapi
  tts: { provider: cartesia, model: null, voice_id: null }   # null until Stage 4; see rule below
  stt: { provider: null, model: null, language: multi }     # set from the Stage 4 benchmark (ADR 0006)
  filler_lines: true
  max_call_minutes: 15

llm:
  provider: gemini                   # gemini | claude | openai
  model: "gemini-model-id"           # real model ID set here per client; the API key lives in .env
  temperature: 0.3

embeddings:
  provider: gemini                   # gemini | openai | local
  model: "embedding-model-id"
  dimensions: 768                    # fixed for the template: the pgvector column is 768; models must output 768

channels:
  voice: true
  web_chat:
    enabled: true
    allowed_domains: ["https://www.example-developer.in"]   # CORS allow-list for the widget
  whatsapp:
    enabled: true
    opt_out_keywords:
      en: ["STOP", "UNSUBSCRIBE"]
      hi: ["बंद करो", "बंद", "रोको"]
  email: true

sales_team:
  - { name: "Rahul", phone: "+919000000001", email: "rahul@example.com", role: sales_staff, languages: [en, hi] }
escalation:
  human_transfer_number: "+919000000002"
  working_hours: { days: [mon, tue, wed, thu, fri, sat], start: "10:00", end: "19:00" }
  after_hours: take_details_and_callback

site_visits:
  days: [sat, sun]
  slots: ["11:00", "14:00", "16:00"]
  min_lead_hours: 24
  max_days_ahead: 14
  slot_capacity: 3                   # max visits per project per slot
  confirmed_by: [client_admin, sales_staff]

lead_scoring:
  weights: { budget_match: 30, location_match: 20, timeline_under_6m: 20, loan_ready: 10, visit_requested: 20 }
  thresholds: { hot: 70, warm: 40 }
  qualified_requires: [budget, location, property_type]

follow_up:
  schedule_days: [1, 3, 7]
  channels: [whatsapp]               # "voice" is rejected while outbound_calls_enabled is false

compliance:
  ai_disclosure: { en: scripts/en/ai_disclosure.md, hi: scripts/hi/ai_disclosure.md }
  recording_disclosure: { en: scripts/en/recording_disclosure.md, hi: scripts/hi/recording_disclosure.md }
  outbound_calls_enabled: false      # requires DND/TRAI review before enabling

truth:
  stale_after_days: 30
  block_on_stale: [price, availability, offer, payment_plan, possession, area]   # all six fact types

retention:
  transcript_days: 365
  recording_days: 180

usage_limits:
  monthly_call_minutes: 3000
  monthly_llm_usd: 100
  alert_at_percent: [50, 80, 100]
  on_limit: transfer_to_human        # transfer_to_human | pause
```

**Validation rules that settle edge cases**

- All phone numbers are E.164. Sample numbers use the clearly fake `+9190000000NN` range.
- `voice.tts` and `voice.stt` fields may be `null` until Stage 4. Config still loads; the `assistant-request` handler refuses to build a live assistant (and transfers to the human number) while any of them is `null`.
- `follow_up.channels` may contain `voice` only when `compliance.outbound_calls_enabled` is `true`.
- Every file in "Script files" must exist for every language in `agent.languages`. For `whatsapp/`, every language must contain the same set of template file names.
- Fact types use one enum everywhere (config, Truth Layer, tools, tests): `price`, `availability`, `offer`, `payment_plan`, `possession`, `area`.
- WhatsApp opt-out matches only when the whole message (trimmed, case-insensitive) equals a keyword. A keyword inside a longer sentence never unsubscribes anyone.
- Embedding dimensions are fixed at 768 for the template. Changing them is a template change with a new migration, not a per-client config change.

**Script files** (one copy each in `client/scripts/en/` and `client/scripts/hi/`; these exact names are what the validator checks)

`greeting.md`, `ai_disclosure.md`, `recording_disclosure.md`, `sales_script.md`, `objections/price.md`, `objections/location.md`, `objections/think_about_it.md`, `objections/competitor.md`, `filler_lines.md`, `follow_up.md`, `paused_handoff.md` (chat reply when the agent is paused or over limit), `after_hours.md`, `voice_transfer.md` (said on a call just before transferring to a person), `whatsapp_unsupported.md` (reply to a WhatsApp voice note, photo or file), `customer_name_fallback.md` (used in a WhatsApp greeting when the name is unknown), `whatsapp/*.md` (one file per template).

Secrets (Vapi key and webhook secret, LLM key, WhatsApp token, email credentials, database URL, session secret) live only in `.env`; `.env.example` lists every one.

## Database schema overview

Single-tenant PostgreSQL with no `tenant_id` and no RLS; structured commercial facts live in normal tables, and pgvector holds only document chunks.

| Area | Tables | Key points |
| --- | --- | --- |
| Auth | `users`, `sessions`, `audit_log` | Roles: `client_admin`, `sales_staff`. Audit on approvals, visit confirmations, config changes, exports |
| Data Center | `uploads`, `import_batches`, `import_rows`, `validation_errors` | Every fact links back to its batch and source file |
| Inventory (Truth) | `projects`, `towers`, `units`, `unit_types`, `price_lists`, `offers`, `payment_plans`, `amenities` | Each row has `status` (draft/approved/archived), `approved_by`, `approved_at`, `valid_from/to`, `version`. Staleness is computed, not stored: stale when `approved_at` is older than `truth.stale_after_days` or `valid_to` has passed. Re-approval clears it automatically. Only `approved` + not stale is served |
| Knowledge | `documents`, `document_chunks` (pgvector) | Brochures, FAQs, legal docs. Never used for prices or availability |
| Customers | `customers`, `customer_identities` | Identities = phone, WhatsApp, email, web session. `preferred_language`. Merge only on verified match; uncertain matches flagged for staff |
| Conversations | `conversations`, `messages`, `calls` | `channel`, `language`, language switches per message. `calls` holds Vapi call ID, recording URL, transcript, summary, duration, outcome, cost |
| Leads | `leads`, `lead_requirements`, `lead_events`, `lead_matches`, `objections` | State machine transitions logged in `lead_events`. Score + reason stored per evaluation |
| Visits | `site_visits`, `site_visit_events` | States: requested, confirmed, completed, rescheduled, cancelled, no_show. `confirmed_by` must be a user |
| Handoff | `handoffs`, `follow_ups` | Assigned salesperson, context snapshot, due date, channel |
| Webhooks | `webhook_events` | Provider event IDs for dedupe/idempotency and replay protection |
| Usage | `usage_records`, `agent_state` | Per call/message/LLM request cost. `agent_state` holds the kill switch |

Indexes on phone/email lookups, `units(project_id, status, bhk, price)`, lead status + score, and visit date. Migrations via Alembic only; a retention job purges or anonymizes transcripts and recordings after a configurable period.

## Build order

The single build runs in eight stages; stages 1–5 are the vertical slice (the demo milestone), and 6–8 complete every remaining phase on top of it. Each stage ends with its tests passing in CI before the next starts. Detailed prompts per stage are in `ESTARA_STAGE_PROMPTS.md`.

1. **Foundation — Phases 1–3**
    - Write `ESTARA_MASTER_SPEC.md` and all docs from section 3 of the brief, plus ADRs 0001–0005 (clone model, stack, Vapi assistant-request, bilingual/Hinglish policy, Truth Layer + deterministic scoring). The transcriber ADR (0006) waits for the Stage 4 benchmark.
    - Repo skeleton, Docker Compose (Postgres+pgvector, Redis, backend, worker, dashboard), CI (ruff, mypy, pytest, eslint, tsc).
    - Config schema + loader + validation, `client/` folder with sample `en`/`hi` scripts.
    - Full schema and Alembic migrations. Auth with two roles, sessions, rate limits, audit log.
    - *Done when:* `docker compose up` runs, migrations apply on an empty DB, invalid config fails with a clear error, auth tests pass.
2. **Data and truth — Phases 4–5**
    - Data Center: CSV/Excel templates, upload → parse → validate → preview → approve → import, duplicates, versions, computed staleness with a daily alert job. PDF/DOCX go to the knowledge store.
    - Truth Layer API serving only approved, non-stale facts, plus the `formatters` module (lakh/crore, Hindi number words, dates).
    - *Done when:* sample inventory imports end to end; a price query on unapproved or stale data returns "confirm with team"; formatter tests cover en and hi.
3. **Customers and brain — Phases 6–8**
    - Identity resolution, conversations, memory, language preference.
    - LLM adapter (Gemini, with Claude/OpenAI behind the same interface), Sales Brain prompts in en/hi, requirement extraction, objection handling from script files.
    - Structured property matching with explainable ranking. Lead state machine and deterministic scoring from config.
    - *Done when:* text-only test conversations in English, Hindi and Hinglish produce correct requirements, matches, scores and a visit request — with no price that isn't from the Truth Layer.
4. **Voice — Phases 9–10**
    - Vapi tools endpoint with signature verification, replay protection and contract tests. `assistant-request` handler generating the assistant from config. `end-of-call-report` ingestion.
    - Transcriber benchmark on recorded Hinglish samples; lock the choice in an ADR. Cartesia Sonic voice configured from config.
    - *Done when:* mock Vapi payload tests pass, then a real Vapi web/test call in both languages creates a lead with transcript and summary. Real phone-number calls are marked blocked until a number is connected.
5. **Visits and handoff — Phase 11** → **demo milestone**
    - Visit request, staff confirmation, reschedule/cancel/no-show, handoff package, follow-up jobs.
    - Minimal dashboard pages needed to see the result: leads, lead detail, calls, visits.
    - *Done when:* upload sample data → call in Hindi → lead, call summary, matched property and visit request appear; staff confirms the visit.
6. **Remaining channels — Phases 12, 14, 15**
    - Website widget (one script tag, bilingual UI). WhatsApp Cloud API adapter (verification, dedupe, retries, status, opt-out, en/hi templates). Email adapter (OAuth/app password, threading).
    - *Done when:* a customer who called then messages on WhatsApp lands in the same customer record with one unified thread.
7. **Full dashboard and controls — Phases 13, 16**
    - All dashboard screens from the brief with real APIs and loading/empty/error states. Usage and cost tracking, limits, alerts, kill switch.
    - *Done when:* Playwright e2e covers every screen; flipping the kill switch makes the next call transfer to the human number.
8. **Hardening and shipping — Phases 17–20**
    - Security review (authz on every route, webhook checks, secret scanning, log redaction). Observability, backups, a tested restore.
    - `setup_client.sh`, clone guide, template update procedure, clone rehearsal on a fresh machine.
    - End-to-end evaluation suite (scripted calls in en/hi/Hinglish, scored for accuracy, visit-push rate, hallucination = 0).
    - *Done when:* a fresh clone with only config, `.env` and data changed passes the full acceptance checklist except items needing real external accounts.

## Vapi tool contract

All tools go through `POST /api/v1/vapi/tools`, take the call ID plus a `language` field, and return short, speakable results in that language with numbers pre-formatted by the Truth Layer.

| Tool | Input | Returns | Side effects |
| --- | --- | --- | --- |
| `get_customer_context` | caller phone | Returning-caller summary, preferred language, open lead/visit | None |
| `search_properties` | budget, location, type, BHK, timeline | Top 3 matches with reason per match | Saves matches to lead |
| `get_property_details` | project/unit ID | Approved details, or "confirm with team" flags | None |
| `get_commercial_info` | project ID, fact type (one of the fact-type enum values) | Approved, non-stale facts as spoken strings | None |
| `search_knowledge` | question | Up to 3 approved document snippets | None |
| `save_requirements` | extracted requirements | Ack + missing fields to ask next | Updates lead requirements |
| `evaluate_lead` | lead ID | Score, band, reason (deterministic) | Stores evaluation |
| `get_visit_slots` | project ID | Next available slots from config | None |
| `request_site_visit` | project, slot, name | "Requested, team will confirm" | Creates visit (requested), notifies sales |
| `transfer_to_human` | reason | Transfer destination | Creates handoff record |
| `schedule_follow_up` | when, channel | Ack | Creates follow-up job |

Every response stays under ~300 tokens. Unknown or failed lookups return a structured `unavailable` result the assistant is prompted to turn into "I'll confirm that with our sales team," never an error read aloud.

## Testing and verification

Every stage ships with automated tests, and Hindi gets the same coverage as English — not a few spot checks at the end.

- **Unit:** config validation, formatters (lakh/crore, Hindi words, dates), scoring rules, lead and visit state machines, matching rank order.
- **Integration:** Data Center import paths, Truth Layer refusal on stale/unapproved data, identity merge rules, webhook signature and replay rejection.
- **Contract:** recorded Vapi, WhatsApp and email payloads replayed against handlers; tool response shape and size checks.
- **Conversation evals:** a scripted set of at least 30 text conversations (10 English, 10 Hindi, 10 Hinglish) covering budget mismatch, objections, competitor mentions, asking for a human, and trick questions about prices not in the data. Pass bar: zero invented prices or dates, correct language in every reply, visit offered in every qualified conversation.
- **Voice tests:** real Vapi test calls by native Hindi speakers, with recorded latency, transcript accuracy on numbers and locality names, and pronunciation of amounts and project names. Results logged with call IDs.
- **Dashboard e2e:** Playwright across all screens and both roles.
- **Recovery:** restore the latest backup to a fresh database and confirm the dashboard loads the restored leads.

The report for each stage records exact commands, results and call IDs, and labels each feature *implemented*, *tested*, or *externally blocked*.

## External dependencies you must provide

The build can proceed without these, but each one gates a specific acceptance item; the earlier you start the slow ones (Indian number, WhatsApp approval), the sooner the build is fully verified.

| Item | Needed for | Lead time | Notes |
| --- | --- | --- | --- |
| Vapi account + API key + webhook secret | Stage 4 voice | Same day | Free tier for sample data only |
| Cartesia voice selection in Vapi | Stage 4 | Same day | Confirm the Sonic model ID and a voice that sounds natural in both languages |
| Gemini API key | Stage 3 | Same day | Free key for development only |
| Indian phone number reachable by Vapi (forwarding or SIP via an Indian provider) | Real test call | Days to weeks | Provider KYC applies; confirm availability before promising a date |
| Hindi/Hinglish sample call recordings (with consent) | Transcriber benchmark | 1–2 days | 15–20 short clips of real enquiry phrasing |
| Native Hindi speakers for test calls | Voice verification | Ongoing | At least 2 people, different accents |
| Meta WhatsApp Business account + verified number + approved templates (en, hi) | Stage 6 WhatsApp | 1–3 weeks | Template approval is per language |
| Email account with OAuth or app password | Stage 6 email | Same day | |
| Sample property data (CSV/Excel) and brochures | Stage 2 onward | Same day | Fake or sample data on free tiers |
| Hosting accounts (Render/Railway/Fly/VPS, Vercel) | Stage 8 | Same day | Paid tiers required before real customers |
| Legal review of recording/AI disclosure text and India telecom rules | Pilot | 1–2 weeks | The build documents requirements; it does not claim compliance |

## Pilot, production and acceptance

After the build, run a 2–4 week controlled pilot with one client (Phase 21) before calling anything production-ready (Phase 22).

**Pilot setup:** paid tiers on all providers, client's real data approved by the client, call forwarding for part of the day first, a salesperson reviewing every handoff, and a weekly review of transcripts in both languages.

**Pilot metrics to watch:** share of calls fully handled without a transfer, visit-request rate on qualified calls, invented-fact incidents (must be zero), Hindi transcription accuracy on numbers and localities, average silence per turn, and cost per call.

**Final acceptance checklist**

- [ ] Fresh clone deployed by changing only `client.config.yaml`, `.env` and data
- [ ] Client uploads and approves its own property data
- [ ] Real test call verified on the client's number (forwarding or SIP)
- [ ] Agent answers in English, Hindi and Hinglish using only approved data, qualifies, and pushes for a visit
- [ ] Lead, call details, requirements, matched properties and visit request visible on the dashboard
- [ ] Human confirms the visit and receives a full-context handoff
- [ ] Website, WhatsApp and email share the same customer record
- [ ] Kill switch, usage and cost view, backup restore, and human-number fallback all verified
