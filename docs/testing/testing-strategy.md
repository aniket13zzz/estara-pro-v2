# Testing strategy

Every stage ships its tests. Hindi gets the same coverage as English; every suite that covers
conversation behaviour includes English, Hindi and Hinglish cases.

## Layers

| Layer | Where | What | From |
| --- | --- | --- | --- |
| Unit | `backend/tests/unit/` | Config validation, log redaction, route guard (static); later formatters, scoring, state machines, ranking | 1 |
| Integration | `backend/tests/integration/` | Migrations on an empty DB, auth flows, route guard (HTTP); later imports, Truth Layer refusals, identity merge, webhooks | 1 |
| Contract | `backend/tests/contract/` | Recorded Vapi / WhatsApp / email payloads replayed against handlers | 4, 6 |
| Conversation evals | `tests/evals/conversations/`, `make eval` | ≥ 30 scripted conversations (10 en, 10 hi, 10 Hinglish), live LLM; zero invented facts | 3 |
| Dashboard e2e | `dashboard/e2e/`, `make test-e2e` | Playwright, all screens, both roles | 1 (smoke), 5, 7 |
| Voice | Real Vapi calls | Latency, transcript accuracy on numbers and localities, pronunciation; call IDs recorded | 4 |
| Recovery | Stage 8 | Restore latest backup to a fresh DB; dashboard loads restored leads | 8 |

## Database tests

Tests that need PostgreSQL read `TEST_DATABASE_URL` (a pgvector-enabled database they may wipe).
They **fail**, rather than skip, if it is not set, so a missing database can never look like a
pass. The migration test creates and drops its own empty database (`estara_migration_test`), so
the role needs `CREATEDB`.

## Commands

- `make check` — ruff (lint + format check), mypy strict on `app/`, pytest, ESLint, `tsc`.
- `make test-e2e` — Playwright against a running stack. Set `PLAYWRIGHT_CHROMIUM_PATH` to use an
  already-installed Chromium instead of `npx playwright install chromium`.
- `make eval`: the conversation evals on the live LLM (see below). It needs the provider key
  in `.env` and exits 2 without it. Use `EVAL_ARGS="--style hi"` for one language,
  `EVAL_ARGS="--id en_01_new_town_visit"` for one conversation, or `--list` to see the ids. `EVAL_ARGS="--llm offline"` runs the same pipeline with the
  labelled offline stand-in as a plumbing check; that is never an eval result.
- `make chat CHAT_LANG=hi`: talk to the Sales Brain in a terminal (`CHAT_LLM=offline` without
  a key, `CHAT_ARGS=--debug` to see tools, lead state and guard results).

## Conversation evals (Stage 3)

`backend/tests/evals/conversations/*.yaml` holds 30 conversations: 10 English, 10 Hindi
(Devanagari) and 10 Hinglish (Roman). Each lists the customer's turns, the expected reply
language per turn, and what must happen: qualify and be offered a visit, handle facts the data
does not have, or hand off with a reason. Coverage includes budget mismatch, price, location,
think-about-it and competitor objections, requests for a person, a legal question, anger,
off-topic messages, AI-identity questions, language switches both ways, and trick questions
about prices not in the data (villas, 4 BHK, offers that were never approved).

`python -m tests.evals.run` (`make eval`) migrates and **wipes** the database in
`EVAL_DATABASE_URL` (default `TEST_DATABASE_URL`) and loads the fake sample inventory. It then
runs each conversation through `SalesBrain.respond` and applies `tests/evals/checks.py`:

| Check | Pass bar |
| --- | --- |
| `numbers_grounded` | No number or date in any reply that a tool result, the customer or script text did not give. Two readers must agree: the guard's quantity reader and a plain digit check |
| `language` | Every reply in the expected language (Hinglish → Hindi in Devanagari) |
| `visit_offered` | Conversations that should qualify do, and a later reply offers slots that `get_visit_slots` returned |
| `unavailable_handled` | A tool returned `unavailable` and that reply says the team will confirm |
| `handoff` | A handoff with the expected reason exists |
| `no_ai_failure` | No reply fell back because the LLM failed |

**Free-tier pacing and rate limits.**
- At least `--llm-delay` seconds between LLM calls (default 10, or `EVAL_LLM_DELAY_SECONDS`;
  the free-tier limits for your project are shown in Google AI Studio).
- An HTTP 429 is retried up to `--rate-limit-retries` times (default 6), waiting what the
  provider asks or backing off, with single waits up to `--max-wait` seconds (default 90).
- A conversation that is still rate limited is reported as **"rate limited, not run"** and is
  not scored.
- A response slower than `--llm-timeout` (default 120 s, or `EVAL_LLM_TIMEOUT_SECONDS`) is
  retried once. If it is still slow, the conversation is **"timed out, not run"**.
- An HTTP 5xx (provider outage, or Gemini's 503 "This model is currently experiencing high
  demand") is retried 3 times with waits of about 10, 20 and 40 s. If it persists, the
  conversation is **"provider unavailable, not run"**.
- A daily quota, or three of these provider problems in a row, stops the run; the rest are
  "not run".
- The report counts every LLM request sent (answered, rate limited, timed out, unavailable) per
  conversation and in total, plus the average requests per customer turn. Use this to plan
  runs against a daily quota.
- Exit codes: 0 every selected conversation ran and passed; 1 at least one failed; 3 nothing
  failed but some did not run (rate limited, timed out, provider unavailable or stopped); 2 not
  runnable.
- The 30 conversations have 65 customer turns, each taking about 2–4 LLM calls, so a full
  run at 10 s takes roughly 25–45 minutes. One language takes roughly 10–15 minutes.

The report also counts how often the model's first draft broke a rule and the guard sent it
back, so the guard is never credited to the model. Results are written to
`backend/tests/evals/results/` (not committed); the summary of each recorded run is copied
into `docs/progress.md`.

## Stage 1 coverage

- Config: valid sample; voice readiness; Devanagari present in every Hindi script; missing,
  empty and mismatched Hindi files (including WhatsApp templates); disclosures per language;
  bad phones (4 forms); bad slots (4 forms); duplicate slots; working-hours order; threshold
  order; voice follow-ups vs outbound; embedding dimensions; default language; opt-out keywords
  per language; unknown fact type; unknown keys; all problems reported in one pass; startup
  exits with a clear message for an invalid config or a missing `SESSION_SECRET`.
- Migrations: all 34 planned tables; no `tenant_id`; Truth columns present and no stored
  staleness; language columns; upgrade → `alembic check` (no drift) → downgrade → upgrade on an
  empty database; approval and status constraints enforced by the database.
- Auth: argon2 hashes; password length; login with secure cookies and `/me`; only token digests
  stored; identical response for wrong password and unknown user; inactive users; anonymous
  rejection; lockout and recovery; per-IP rate limit; CSRF required; cross-origin rejected;
  expired sessions; role checks; public health.
- Route guard: static dependency check (with a self-test that a leaky route is caught) and an
  HTTP check over every OpenAPI operation.
- Dashboard smoke (Playwright): status page, wrong-password error, admin sign-in.

## Stage 2 coverage

- Formatters (91 cases): Indian grouping; English lakh/crore including 1 lakh, 99 lakh, 1 crore,
  1.05 crore, 1.5 crore, 12.75 crore, exact-or-full-form (never rounded), ranges; Hindi words
  with सवा/डेढ़/ढाई/साढ़े/पौने and the plan's examples; months, full dates, areas, percents,
  counts; no grouped digits ever reach a spoken string.
- Validation (pure): every rule — missing column, unknown column/sheet, empty file, required,
  price > 0, lakh/crore words rejected, whole rupees, area > 0, date format and real dates,
  whole-number BHK, choices, formula-like text, length, valid_to ≥ valid_from, percent range,
  conflicting fields, price range, milestones adding to 100, unknown project/unit type/unit,
  references to unapproved parents (warning), archived parents, duplicates in file,
  contradictions (same unit different price; unit vs unit price list; unit BHK vs unit type),
  unchanged vs changed against approved data, summary counts.
- Scanning: type from content, extension mismatch, empty/binary/non-UTF-8, macros, zip-bomb
  ratio, corrupt zip, CSV entity detection, workbook sheets, templates with README.
- Data Center API (DB): sample inventory end to end; one-batch workbook; preview errors block
  approval and write nothing; versioning (v1 archived, v2 approved, source batch kept); new
  project version keeps children linked; only `client_admin` approves/rejects; double
  approval refused; reject archives drafts; archive withdraws facts; template downloads;
  freshness and properties under a moved clock; `seed` imports through the pipeline and is
  re-runnable.
- Truth Layer (DB): every fact type served with en/hi spoken strings and sources; refusals for
  each reason code — missing, stale by age, stale by `valid_to`, not-yet-valid, unapproved,
  pending unit price list, cross-batch contradiction, two approved versions; re-approval
  clears staleness; `block_on_stale` respected; client-timezone validity; daily alert job
  opens, updates and resolves one alert.
- Knowledge (DB): only approved documents are searchable, drafts and archived never; at most
  three results; PDF and DOCX extraction; embeddings-not-configured, bad doc type, unknown
  project and no-text errors; chunking.
- Embeddings: Gemini and OpenAI request shapes against the documented APIs (mocked transport),
  normalisation, error handling; offline embedder.
- Dashboard (Playwright, live stack): sample inventory uploaded and approved through the UI,
  Properties shows the prices; a file with errors lists them and cannot be approved; sales
  staff can review but not approve; freshness and uploads lists.

## Stage 3 coverage

- Identity (DB): call then WhatsApp from one number is one customer; same verified email
  merges; Indian phone forms normalised, bad phones refused; must-not-merge cases (phone typed
  into web chat, unverified email, verified claim vs unverified identity, same name with a
  different phone); conflicting verified identifiers flagged instead of merged; the database
  keeps verified identifiers unique.
- Language: detection for en, Devanagari Hindi (including mostly-Latin Hinglish) and Roman
  Hinglish; neutral messages keep the current language; switches recorded.
- Extraction (50 cases): 27 budget phrasings across en/hi/Hinglish (lakh/crore, ranges, "tak",
  "se zyada nahi", ₹ with Indian grouping, "1.2 cr", "80L", Hindi number words, सवा/डेढ़/ढाई),
  numbers that are not budgets, 14 whole messages (BHK, location aliases, timeline, loan,
  purpose, property type).
- LLM adapters (mocked transport): Gemini, Claude and OpenAI request shapes and tool round
  trips; Gemini thought signatures echoed; error classification; retries with backoff; usage
  recording; structured extraction with retry; tools without arguments.
- Leads, matching, slots (DB): every ADR 0007 transition allowed or rejected and logged; staff
  transitions need a user; scoring from config weights; ranking order and en/hi reasons;
  stale prices never matched; slot capacity and lead time.
- Guards and signals (unit): grounded and ungrounded replies in en/hi/Hinglish for each claim
  kind; sentence removal; reply-language check; objection, escalation and AI-question
  detection in all three styles; masking of amounts in document snippets.
- Sales Brain (DB, scripted LLM): English, Hindi and Hinglish flows; unavailable facts;
  ungrounded numbers sent back, then removed, then handed off; wrong language rewritten;
  escalation enforced; transfer not doubled; objection recorded with its script in the
  prompt; AI disclosure; kill switch and usage limit skip the LLM; LLM failure after retries;
  bad tool calls; visit request needs qualification, creates the visit and a handoff;
  follow-up moves the lead to nurturing.
- Eval checkers (unit): each check fails on a bad transcript; the set has 10/10/10
  conversations.
