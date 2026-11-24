# Progress

Each stage appends a report: what was built, exact commands and results, status labels
(**implemented** / **tested** / **externally blocked**), and what the user must provide next.

---

## Stage 1 — Foundation (Phases 1–3)

Date: 2026-10-11. Commits on `main` in this workspace (not yet pushed; no GitHub repo was
reachable from the session).

### Inputs

- Read: `CLAUDE.md` (revision 2), `docs/roadmap/phase-plan.md` (revision 2),
  `ESTARA_STAGE_PROMPTS.md`.
- **Missing:** `ESTARA_PRO_BUILD_PROMPT.md` (the brief) was not received. The master spec was
  written from the three files above; items that would normally come from the brief (lead
  transition table, user-journey details) are marked **[inferred]** in `ESTARA_MASTER_SPEC.md`.

### What was built

1. **Docs:** `ESTARA_MASTER_SPEC.md`; `docs/architecture/architecture.md`,
   `docs/architecture/database-design.md`, `docs/security/security-design.md`,
   `docs/integrations/integration-design.md`, `docs/testing/testing-strategy.md`,
   `docs/deployment/deployment-guide.md`, `docs/clone-guide.md` (skeleton), this file;
   ADRs 0001–0005 in `docs/decisions/`; `README.md`.
2. **Repo foundation:** `backend/` with all 17 modules (empty ones have a one-line docstring);
   `dashboard/` Next.js 16 App Router, TypeScript strict, `/login` placeholder (wired to the real
   auth API) and `/health` status page, `/api` proxied to the backend; `docker-compose.yml`
   (pgvector pg16, redis, backend, ARQ worker, dashboard); `Makefile` (`up`, `migrate`,
   `seed-sample`, `check`, `test-e2e`, `eval`, plus `install`, `config-check`, `down`, `logs`);
   GitHub Actions CI (ruff, mypy, pytest with a pgvector service, ESLint, tsc, dashboard build);
   `.env.example` with every variable; `.gitignore` excludes `.env`.
3. **Config system:** `client/client.config.yaml` exactly per the plan; 13 required script
   files + 2 WhatsApp templates in both `scripts/en/` and `scripts/hi/` (Hindi in Devanagari
   Hinglish); `backend/app/core/config.py` validates every plan rule (E.164, nullable voice
   fields, voice follow-ups vs outbound, all script files per language, same WhatsApp template
   set per language, valid and unique slots, ordered thresholds and working hours, embeddings
   fixed at 768, one fact-type enum, opt-out keywords and disclosures per language, unknown keys
   rejected). Schema errors and missing files are reported together in one message. The API
   (`uvicorn app.asgi:app`) and the worker exit with code 1 and that message on an invalid
   config.
4. **Database:** SQLAlchemy models for all 34 planned tables and Alembic migration `0001`
   (creates the `vector` extension). Commercial tables carry status / approved_by /
   approved_at / valid_from / valid_to / version / source_batch_id with database constraints;
   no stored staleness. Language on conversations and messages, `preferred_language` on
   customers. Plan indexes, plus an HNSW cosine index on `document_chunks.embedding`.
5. **Auth:** argon2id hashing; server-side sessions with HMAC-digested tokens in HttpOnly,
   Secure, SameSite=Lax cookies; CSRF double-submit token on state-changing requests plus an
   Origin check; per-IP login rate limit (Redis) and per-account lockout; roles `client_admin`
   and `sales_staff` with `CurrentUser` / `StaffUser` / `AdminUser` dependencies; audit helper
   (login, failures, lockout, logout, user creation are audited); user-creation CLI; log
   redaction of phones, emails and tokens.

### Commands and results

| Check | Command | Result |
| --- | --- | --- |
| Lint, format, types, tests | `make check` | **exit 0.** ruff: "All checks passed!", "48 files already formatted"; mypy strict: "Success: no issues found in 37 source files"; pytest: **67 passed in 22.46s**; `eslint .` and `tsc --noEmit`: no output, exit 0 |
| Migrations on an empty DB | `DATABASE_URL=…/estara_fresh alembic upgrade head` on a newly created database | "Running upgrade -> 0001", exit 0, 34 tables created |
| Upgrade/downgrade/drift | `tests/integration/test_migrations.py` | upgrade → `alembic check` ("No new upgrade operations detected") → downgrade to 0 tables → upgrade again: pass |
| Invalid config stops startup | `ESTARA_CONFIG_PATH=<broken copy> uvicorn app.asgi:app` | exit 1; output: "Invalid client config … 3 problem(s) found: escalation.human_transfer_number: phone number '+91XXXXXXXXXX' is not E.164 …; site_visits.slots: time '25:00' is not a valid time of day; missing file: scripts/hi/objections/competitor.md not found" |
| Services run and talk to each other | Native substitute for `make up` (see below): uvicorn, `arq`, `next start`, local Postgres 16 + pgvector 0.8.0, Redis 7.0 | `/api/v1/health` → `{"status":"ok","checks":{"database":"ok","migration":"0001","redis":"ok"},…}` directly and through the dashboard proxy; worker logged "Worker started for Sunrise Developers" and returned `ok` for an enqueued `heartbeat` job; `/login` → 200 |
| Dashboard e2e smoke | `PLAYWRIGHT_CHROMIUM_PATH=… make test-e2e` | **3 passed**: status page, wrong-password error, admin sign-in |
| Docker Compose stack | `make up` | **Failed here:** `registry-1.docker.io … Forbidden` — this workspace's network policy blocks Docker Hub. `docker compose config --quiet` validates the file |
| Eval suite | `make eval` | exit 2 by design: "built in Stage 3" |

Test breakdown (67): config validation 32, auth 15, migrations and schema 7, route guard 5
(static 3, HTTP 2), log redaction 8.

### Status

| Item | Status |
| --- | --- |
| Config schema, loader, all validation rules, startup failure | **tested** |
| en/hi script files (sample text) | **implemented**; presence and Devanagari **tested**. Disclosure texts need legal review |
| 34-table schema and migration 0001 | **tested** (empty-DB upgrade, downgrade, drift, constraints) |
| Auth: login, argon2, sessions, CSRF, rate limit, lockout, roles | **tested** |
| Audit log helper | **tested** (auth events) |
| Route-auth guard (static + HTTP) | **tested**, including a self-test that catches a leaky route |
| Log redaction | **tested** |
| Dashboard login placeholder and health page | **tested** (Playwright smoke, ESLint, tsc, `next build`) |
| ARQ worker start-up | **tested manually** (heartbeat job), no automated test |
| `make up` (Docker Compose) | **tested** — user-reported on Windows, 2026-10-12: `docker compose up` ran all services. (Blocked in the build workspace: Docker Hub denied by egress policy.) See the follow-up below |
| `make migrate` | **tested** — user-reported on Windows: `alembic upgrade head` succeeded against the Compose database |
| `make seed-sample` / admin sign-in | Admin sign-in at localhost:3000 **tested** (user-reported). `make seed-sample` now calls `python -m app.seed` (follow-up below) |
| GitHub Actions CI | **implemented**; every CI step replayed in a clean clone here and passed. The real GitHub run result was not received (see follow-up) |
| Backend and dashboard Dockerfiles | **tested** — built during the Windows `docker compose up`. The backend Dockerfile was restructured afterwards (follow-up) |

### Decisions and deviations

- Database driver is psycopg 3 for both the app and Alembic (one driver). Recorded in ADR 0002.
- Health endpoints, login, logout, `/auth/me` and an admin-only `GET /api/v1/users` were added:
  the first three are needed for the dashboard placeholder, and the user list gives the role
  checks a real admin-only route. Login/logout/user-creation audit entries go beyond the
  CLAUDE.md minimum; they are security events.
- FastAPI 0.143 includes routers lazily, so a plain walk of `app.routes` found no routes. The
  guard test's sanity count caught this; the guard now checks statically (descending into
  included routers) and over HTTP for every OpenAPI operation.
- The Playwright config accepts `PLAYWRIGHT_CHROMIUM_PATH` because the browser download host is
  blocked here; default behaviour is unchanged.
- `make eval` exits 2 until Stage 3 so it can never look like a passing eval.

### What you need to provide next

*(Items 1–3 of the original list are resolved or tracked in the follow-up below.)*

4. For Stage 2: sample property data as CSV/Excel and 1–2 brochure PDFs (or I generate clearly
   fake sample data, as the stage prompt allows).
5. Start the slow items now: Indian number / SIP provider KYC and WhatsApp template approvals.

### Stage 1 follow-up (2026-10-12)

**Docker verification (user-reported, Windows).** The user ran the stack on Windows with Docker
Desktop: `docker compose up` started all services, `alembic upgrade head` applied migration
0001, and admin sign-in at http://localhost:3000 worked. These results were reported by the user;
they were not observed from the build workspace, where Docker Hub is blocked.

**CI.** The user's message carried the template text "[passed / paste error]" instead of a
result, so the outcome of the GitHub Actions run is unknown. Every CI step was replayed here from
a fresh `git clone` with a new virtualenv, no `.env` and no build artefacts:

| Step | Result |
| --- | --- |
| `pip install -e ".[dev]"` | ok |
| `ruff check . && ruff format --check .` | "All checks passed!", "48 files already formatted" |
| `mypy` | "Success: no issues found in 37 source files" |
| `python -m app.core.config` | "Client config OK: Sunrise Developers, languages ['en', 'hi'] …" |
| `pytest` (pgvector database) | 67 passed |
| `npm ci`, `npx eslint .`, `npx tsc --noEmit`, `npm run build` | all exit 0 |

No CI failure was found, so nothing was changed for CI. If the GitHub run failed, its error is
still needed.

**Lead transitions.** The brief lists the nine lead states but not every transition. They are
now defined in `docs/decisions/0007-lead-state-transitions.md` (0006 stays reserved for the
Stage 4 transcriber ADR) and summarised in `ESTARA_MASTER_SPEC.md` §3.2. The brief itself has
still not been available in a build session, so the spec has not been compared with it line by
line.

**Windows support.** `docs/deployment/deployment-guide.md` has a "Windows (no `make`)" table with
the `docker compose` / `npx` equivalent of every target. Supporting changes:

- `backend/Dockerfile` is now multi-stage: `runtime` (default, unchanged behaviour) and `dev`
  (adds ruff, mypy, pytest).
- New Compose service `checks` (profile `tools`): runs the backend checks in Linux against the
  `estara_test` database — `docker compose --profile tools run --rm --build checks`. Also
  `make check-docker`. Host-side Python is not supported on Windows because psycopg's async mode
  needs a selector event loop.
- `make seed-sample` now runs one command, `python -m app.seed`, which reads `SEED_*` from the
  container's `.env`. The same command works on Windows without `make`.
- These Docker changes are validated with `docker compose config` only; Docker Hub is still
  blocked here. **Re-run `docker compose up -d --build --wait` once on Windows** to confirm the
  restructured image, and try `docker compose --profile tools run --rm --build checks`.

---

## Stage 2 — Data Center and Truth Layer (Phases 4–5)

Date: 2026-10-12. Commits `78bfdb2` (backend), `4d2850c` (dashboard, Compose), plus the docs
commit. Not yet pushed (no GitHub repo connected to the session).

### What was built

1. **Data Center** (`backend/app/datacenter/`)
   - Templates: one CSV per data type and a workbook with every sheet plus a README sheet
     (columns, required, type, units in INR and square feet, YYYY-MM-DD dates, examples).
   - Workflow: upload → scan → parse → validate → preview → approve/reject → import, with
     archive for approved batches. CSV and XLSX are data; PDF and DOCX go to the knowledge
     store.
   - Scanning: file type from content, extension must match, size limits (10 MB data, 25 MB
     documents), XLSX/DOCX macro, part-count, size and compression-ratio checks, UTF-8 CSV only,
     5,000 rows per sheet, files stored by SHA-256.
   - Row-level validation: required fields, types, price > 0, area > 0, dates, choices,
     milestones adding to 100, formula-like text, unknown project/unit type/unit references,
     duplicates within the file, contradictions (same unit with a different price in one
     batch; unit vs unit price list; unit BHK vs unit type), and comparison with approved data
     (new / changed fields / unchanged).
   - Versioning: every import writes new versions; approval archives the replaced versions;
     nothing is deleted; every fact keeps `source_batch_id`.
   - Approval, rejection and archiving: `client_admin` only, audited.
   - Staleness: computed (`approved_at` vs `truth.stale_after_days`, and `valid_to`); a daily
     ARQ cron job (06:00 client time, also at start-up) opens or resolves one `stale_data`
     dashboard alert.
   - Knowledge store: PDF/DOCX text extraction, paragraph chunking, embeddings through the
     adapter (Gemini, OpenAI, offline `local`), `document_chunks`; only approved documents are
     searchable, at most 3 results.
   - API: upload, list batches, preview with findings and rows, approve, reject, archive,
     template downloads, freshness, properties, alerts, and `GET /api/v1/truth/facts`.
2. **Truth Layer** (`backend/app/truth/`)
   - `formatters.py`: English lakh/crore (exact, never rounded), Hindi number words with
     सवा/डेढ़/ढाई/साढ़े/पौने where exact, Indian digit grouping, en/hi months and dates, areas,
     percents, counts.
   - `service.py`: one function per fact type (`price`, `availability`, `offers`,
     `payment_plans`, `possession`, `area`) returning `{value, display, spoken: {en, hi},
     source, approved_at}`, or `unavailable` with `missing`, `stale`, `unapproved` or
     `contradictory` and a detail.
3. **Sample data**: clearly fake inventory for three Kolkata projects (1/2/3 BHK, 14 units,
   7 price lists, one offer, 4 payment plans, 7 amenities) in
   `backend/tests/fixtures/sample_inventory/`. `make seed-sample` (`python -m app.seed`)
   creates the sample users and imports and approves it through the real pipeline.
4. **Dashboard**: Data Center page (upload, templates, freshness, alerts, uploads list) and
   batch page (summary, errors and warnings table, row preview, approve/reject/archive);
   Properties page (approved projects, unit types, prices, availability, last updated, stale
   warnings). Real APIs, with loading, empty, error and success states. Sign-in now lands on
   the Data Center; the nav shows the signed-in user.
5. **Schema**: migration `0002` (alerts, validation severity, batch filename/summary/archived).
6. **Docs**: ADR 0008 (versioning and Truth Layer details); architecture, database, security,
   integration, deployment and testing docs updated.

### Commands and results

| Check | Command | Result |
| --- | --- | --- |
| Lint, format, types, tests | `make check` | **exit 0.** "All checks passed!", "75 files already formatted", mypy "Success: no issues found in 55 source files", **255 passed in 88.13s**, ESLint and `tsc` clean |
| CI replay from a clean clone | fresh `git clone`, new venv, `pip install -e ".[dev]"`, ruff, mypy, config check, pytest; `npm ci`, eslint, tsc, `npm run build` | all pass; 255 passed |
| Migrations on an empty DB | `alembic upgrade head` on a newly created database | 0001 → 0002 applied; up/down/drift covered by `test_migrations.py` |
| Sample inventory end to end (API) | `test_sample_inventory_imports_end_to_end`, `test_workbook_upload_in_one_batch`, `test_seed_loads_sample_inventory_through_the_pipeline` | pass |
| Sample inventory end to end (dashboard) | `make test-e2e` against the running stack (Chromium) | **7 passed**, including "admin imports the sample inventory end to end through the dashboard" |
| Seed on a fresh database | `python -m app.seed` | "Imported and approved projects.csv: 3 rows (3 new …)" … "amenities.csv: 7 rows (7 new …)"; second run reports all rows "unchanged" |
| Price on unapproved or stale data | `test_unapproved`, `test_pending_price_change_does_not_hide_behind_old_price`, `test_stale_by_age`, `test_stale_by_valid_to` | each returns `unavailable` with `unapproved` / `stale` |
| Formatter tests | `tests/unit/test_formatters.py` | 91 passed (stage asks for ≥ 40) |
| Worker | `arq app.jobs.worker.WorkerSettings` on the seeded DB | start-up cron ran: "Staleness check: 0 stale fact(s)" |
| Large upload via dashboard proxy | 15 MB CSV through `localhost:3000/api` | before fix: HTTP 500 (Next.js truncated the body at 10 MB); after fix: HTTP 422 "CSV files are limited to 10 MB." |

Live Truth Layer output on the seeded data (Sunrise Greens):

| Query | English | Hindi |
| --- | --- | --- |
| Price, unit A-1203 | 72 lakh rupees | बहत्तर लाख रुपये |
| Price, 3BHK | 1.2 to 1.5 crore rupees | एक करोड़ बीस लाख से डेढ़ करोड़ रुपये |
| Availability, 2BHK-A | 2 units available | दो units उपलब्ध हैं |
| Offer | Puja Offer: 2 lakh rupees off, valid till 31 December 2026 | Puja Offer: दो लाख रुपये की छूट, 31 दिसंबर 2026 तक |
| Possession | December 2027 | दिसंबर 2027 |
| Area, 3BHK | 1,250 square feet | बारह सौ पचास स्क्वायर फ़ीट |
| Price, unit Z-999 | unavailable: missing | — |

Test breakdown (255): formatters 91, validation 43, config 32, auth 15, Truth Layer 15,
Data Center API 14, scanning 11, embeddings 9, log redaction 8, migrations 7, knowledge 5,
route guard 5.

### Status

| Item | Status |
| --- | --- |
| CSV/XLSX templates with README | **tested** |
| Upload scanning and parsing | **tested** |
| Row-level validation, duplicates, contradictions | **tested** |
| Versioning (archive, never delete; source batch kept) | **tested** |
| Approval permissions and audit | **tested** |
| Computed staleness and daily alert job | **tested** (job also run live in the worker) |
| Truth Layer: all six fact types, four reason codes | **tested** |
| Formatters en/hi | **tested**. Hindi wording needs a native speaker's review |
| Knowledge store (extraction, chunking, approved-only search) | **tested** with the offline embedder |
| Gemini / OpenAI embeddings | **implemented**, request shapes tested against the documented APIs with a mocked transport. Live calls **externally blocked**: no `GEMINI_API_KEY` / `OPENAI_API_KEY` |
| Document upload with the sample config | Refused with `embeddings_not_configured` until a Gemini key is set (or `embeddings.provider: local` for keyless testing) |
| Dashboard Data Center and Properties pages | **tested** (Playwright, live stack) |
| Docker Compose changes (uploads volume, sample mount, multi-stage Dockerfile, `checks` service) | **implemented**, `docker compose config` valid. Not run: Docker Hub is blocked in the build workspace |
| Antivirus scanning of uploads | Not included. Files are parsed only, never executed |

### Decisions and deviations

- Recorded in ADR 0008: drafts written at validation; a file with any error writes nothing;
  re-importing identical data refreshes freshness; parents resolved by code; last approval
  wins; more than one approved version is `contradictory`; not-yet-valid rows are `missing`;
  spoken amounts always end with "rupees"/"रुपये" and are never rounded.
- Towers are in the schema but not in the templates (the stage prompt does not list them).
- Bug found and fixed while testing: Hindi decimals used quarter words for large numbers
  ("साढ़े नौ सौ अस्सी" for 980.5); now limited to numbers under 100.
- Bug found and fixed while testing: the Next.js `/api` proxy silently truncated bodies over
  10 MB, so 25 MB documents would have failed with a 500. Set
  `experimental.proxyClientMaxBodySize: "30mb"`. The option is experimental in Next.js 16.4.
- You didn't provide sample property data or brochures, so I generated clearly fake sample
  data, as the stage prompt allows. Document tests use small generated PDF and DOCX files.

### What you need to provide next

1. **One Docker run on Windows** after pulling these commits:
   `docker compose up -d --build --wait`, then
   `docker compose run --rm --no-deps backend alembic upgrade head`,
   `docker compose run --rm --no-deps backend python -m app.seed`, and optionally
   `docker compose --profile tools run --rm --build checks`. Docker files changed this stage.
2. **The GitHub Actions result** (or the error), and a repo for me to push to.
3. **A Gemini API key** in `.env` (`GEMINI_API_KEY`) and a real embedding model ID in
   `embeddings.model`, to verify document indexing live. Stage 3 also needs the key for the
   Sales Brain.
4. **Optional:** a real (or realistic) brochure PDF and your own property spreadsheet, to try
   the import on real-world files.
5. **The brief** `ESTARA_PRO_BUILD_PROMPT.md`, still not seen in a build session.

### Stage 2 follow-up (2026-10-12)

- **GitHub Actions:** green (user-reported).
- **Windows Docker re-run** with the Stage 2 Docker changes (uploads volume, sample inventory
  mount, multi-stage Dockerfile): passed (user-reported). The Docker items in the Stage 2
  status table are now **tested**.
- **Live LLM eval (Stage 3):** the build workspace's network policy blocks
  `generativelanguage.googleapis.com`, so `make eval` is run by the user on their machine with
  their Gemini key; results get recorded here.

## Stage 3: Customers, Sales Brain, matching, leads (Phases 6–8), 2026-10-13

Brief checked line by line first: `ESTARA_MASTER_SPEC.md` v1.2 and ADR 0007 were reconciled
with `ESTARA_PRO_BUILD_PROMPT.md`. ADR 0009 records the result: amenities became a Truth
fact type, manual entry was moved to Stage 7, and lead creation became a backend rule.

### What was built

1. **Customers and conversations** (`app/customers`, `app/conversations`)
   - Identity resolution: merges only on an exact verified identifier (E.164 phone, which
     also covers WhatsApp; verified email). Uncertain matches set `possible_duplicate_of` for
     staff. Verified identifiers are unique in the database (partial index, migration 0003).
   - Messages carry channel and language. The backend detects the language, logs switches,
     and keeps the conversation language and `preferred_language` current.
   - Customer memory: requirements, preferences, objections, properties discussed, last
     contact.
2. **LLM adapters** (`app/providers/llm`)
   - Gemini (default), Claude and OpenAI behind one `chat` interface with tools, plus
     `extract` (JSON into Pydantic) and `summarize`.
   - Timeouts; retries with exponential backoff on transient errors only.
   - Tokens, latency and cost (from `llm.pricing`) are written to `usage_records`.
   - `OfflineLLM` is a labelled stand-in for plumbing checks only.
3. **Sales Brain** (`app/brain`, ADR 0010): `SalesBrain.respond(conversation, text)` for
   every channel.
   - Deterministic extraction into the `Requirements` model, using one quantity reader for
     en/hi/Hinglish: "80 lakh", "1 crore tak", "ek se dedh crore", "सवा करोड़ से ज़्यादा
     नहीं", "₹85,00,000", "85L".
   - One prompt template with en/hi blocks, the rules from the stage prompt, the
     language's `sales_script.md`, and per-turn backend notes.
   - 11 tools matching the phase plan's tool contract, with facts as Truth Layer spoken
     strings and `unavailable` → "confirm with the sales team".
   - Grounding guard: every number or date must come from tools, the customer or scripts.
     A failing reply is rewritten once, then trimmed; if nothing safe is left, a person
     takes over.
   - Reply-language guard.
   - Objection detection (price, location, think about it, competitor) using the client's
     objection scripts.
   - Escalation: a request for a person, a legal question or repeated anger always hands
     off. Off-topic messages are steered back.
   - AI disclosure on the first text reply and whenever asked.
   - Kill switch and the monthly LLM budget skip the LLM and reply with the paused-handoff
     script.
4. **Matching** (`app/matching`): structured search over approved, available units, with
   ranking out of 100. Budget fit counts 40, location 30, BHK 20 and possession against the
   timeline 10. Each of the top 3 gets a reason in en and hi.
5. **Leads** (`app/leads`)
   - The ADR 0007 table as data; every transition and rejected attempt goes to
     `lead_events`.
   - Deterministic scoring from config weights, storing score, band and reason.
   - System transitions are driven by the known requirements.
6. **Visits, handoffs, follow-ups** (only what Stage 3 needs; confirmation, notifications and
   sending are Stage 5)
   - Slots come from config with capacity and lead time.
   - `request_site_visit` creates a `requested` visit and moves the lead to
     `visit_requested`. It requires a qualified lead and a name.
   - Handoff records carry requirements, recent messages, matches, score, objections and a
     recommended next action.
   - Follow-up rows go on configured channels only, skip opted-out customers, and move the
     lead to `nurturing`.
7. **`make chat`** (`python -m app.brain.cli`): a terminal conversation in en or hi, with
   `--debug` for tools, lead state and guard results.
8. **Evals**
   - 30 conversations in `backend/tests/evals/conversations/`: 10 en, 10 hi, 10 Hinglish.
   - `make eval` runs them and applies the checkers: numbers grounded (two independent
     readers), reply language, visit offered when qualified, unavailable handled, handoff
     reason, no AI failure.
   - Results go to JSON and markdown.
9. **Docs**: ADR 0010; architecture (brain flow), database (0003, `messages.meta`), LLM
   integration table with doc sources, testing (evals and Stage 3 coverage), deployment
   (chat/eval commands, Windows equivalents).

### Commands and results

| Check | Command | Result |
| --- | --- | --- |
| Lint, format, types, tests | `make check` | **exit 0.** ruff "All checks passed!", "116 files already formatted", mypy "Success: no issues found in 83 source files", **523 passed in 77.61s**, ESLint and `tsc` clean |
| CI replay from a clean clone | fresh `git clone`, ruff, format check, mypy, pytest (imports from the clone) | all pass; **523 passed in 79.14s** |
| Migration 0003 | `alembic upgrade head` on the dev DB; up/down/drift in `test_migrations.py` | applied; tests pass |
| CLI, English | `printf '…' \| python -m app.brain.cli --lang en --llm offline --debug` | Turn 1: disclosure plus "missing: budget", lead `engaged`. Turn 2 ("my budget is around 80 lakh"): lead `qualified`; tools `evaluate_lead`, `search_properties`, `get_visit_slots(SG)`; reply lists "2 BHK Classic at Sunrise Greens (Sample), New Town Action Area II, 72 to 78 lakh rupees, within your budget, in your preferred area, possession December 2027 …" and "Saturday 17 October at 11 am …"; guard clean |
| CLI, Hindi | same with `--lang hi`, "नमस्ते, मुझे Rajarhat में 2 BHK चाहिए" then "budget ek crore tak hai" | Hindi disclosure; turn 2: lead `qualified`; "Sunrise Lakeview (Sample), Rajarhat में 2 BHK Lake, पचासी लाख से बानवे लाख रुपये, आपके बजट में … possession जून 2028 …", "शनिवार 17 अक्टूबर, सुबह ग्यारह बजे …". The offline stand-in's English "missing: budget" line was flagged by the language guard, as it should be |
| CLI with the configured LLM | `python -m app.brain.cli --lang hi` | exit 2: "GEMINI_API_KEY is not set in .env. Add the key to .env, or try --llm offline for a plumbing check." |
| `make eval` without a key | `make eval` | exit 2: "Cannot run the eval: GEMINI_API_KEY is not set in .env." |
| Eval plumbing smoke | `make eval EVAL_ARGS="--llm offline"` | Runner, DB prep and every checker ran over all 30 conversations. **Not an eval result** (no language model): 17/30 "passed". numbers_grounded 30/30, handoff 5/5, visit_offered 14/17, language 22/30, unavailable_handled 0/6. The stand-in never asks for commercial facts and writes English field names, so those failures are expected |

Test breakdown (523; 268 new in Stage 3): formatters 103 (+12 for slots and times),
guards and signals 52, extraction 50, lead state 47, validation 43, config 32, language 25,
identity 23, Sales Brain 19, LLM adapters 18, leads/matching/slots 16, Truth Layer 15,
auth 15, Data Center API 14, scanning 11, embeddings 9, logging 8, migrations 7, eval
checkers 6, knowledge 5, route guard 5.

### Live LLM eval

**Not run yet: externally blocked here.** The workspace network policy blocks
`generativelanguage.googleapis.com`, and there is no key in this environment. As agreed, you
run `make eval` on your machine with your Gemini key. I will record the summary (pass counts
per check, failures, guard interventions) here exactly as it comes out, including failures.
The Stage 3 "done when" item "make eval runs and its results are recorded" stays open until
then.

### Status

| Item | Status |
| --- | --- |
| Identity resolution, must-not-merge, duplicate flags | **tested** |
| Conversations, messages, language detection and switches | **tested** |
| Customer memory | **tested** (through the brain tests) |
| Gemini adapter (chat with tools, thought signatures, usage) | **implemented**, tested with a mocked transport. Live call **externally blocked** (network policy; no key here) |
| Claude and OpenAI adapters | **implemented**, lightly tested (mocked). Live calls **externally blocked** (no keys) |
| Retries, timeouts, usage and cost recording | **tested** |
| Requirement extraction (en/hi/Hinglish, lakh/crore) | **tested** (50 cases; stage asks for 30+) |
| Sales Brain pipeline, tools, guards, escalation, objections, disclosure, fallbacks | **tested** with a scripted LLM (19 end-to-end cases) |
| Sales Brain with a real model | **externally blocked** until `make eval` runs with a key |
| Matching (top 3, en/hi reasons) | **tested** |
| Lead state machine and scoring | **tested** |
| Visit requests, handoff records, follow-up rows | **tested** (Stage 3 scope); staff confirmation and notifications are Stage 5 |
| `make chat` in en and hi | **tested** with the offline stand-in; with Gemini **externally blocked** here |
| `make eval` runner and checkers | **tested** (checker unit tests and the offline plumbing run); live results pending |
| Hindi wording (prompt language block, Hindi slot and time phrases, keyword lists) | **implemented**; needs a native speaker's review |

### Decisions and deviations

- **ADR 0010:**
  - Rules read requirements before the LLM.
  - The grounding guard runs a rewrite, then a trim, then a handoff.
  - Escalation for a request for a person, a legal question or repeated anger is enforced
    by the backend.
  - Disclosure is deterministic.
  - Fallbacks reuse `paused_handoff.md`, so no new script file was needed.
  - The reply token budget leaves room for thinking tokens.
- Hindi detection and the Hindi reply check now need only 15% Devanagari letters (before:
  30%). Real Hinglish keeps project names and loanwords in Latin letters, and "Sunrise
  Lakeview में कौन से payment plan हैं?" is about 30% Devanagari. More Roman Hinglish
  markers were added after the plumbing run showed "dusre builder ne isse sasta offer diya
  hai" detected as English.
- Places typed in Devanagari ("न्यू टाउन") are not read by the rules; the model passes them in
  English letters to `save_requirements`. Conversations hi_01, hi_07 and hi_10 depend on the
  model doing this.
- Hinglish typed in Roman script gets replies in Devanagari, as CLAUDE.md requires. If a
  client wants Roman-script replies on WhatsApp, that needs a config option and an ADR.
- Pulled forward with minimal scope, because the Stage 3 tools need them:
  - visit request rows (Stage 5 confirms them);
  - handoff records (Stage 5 notifies);
  - follow-up rows (Stage 5 sends them);
  - the monthly LLM-budget stop (Stage 7 adds alerts and the dashboard).
- Bug found and fixed while testing: "sawa crore se zyada nahi" was read as a minimum
  budget. A negated "more than" is now an upper limit.
- Gemini requests leave out `parameters` for tools without arguments.

### What you need to provide next

1. **Run the live eval** after pulling, with `GEMINI_API_KEY` in `.env`:
   - Linux/macOS: `make eval`.
   - Windows: `docker compose --profile tools run --rm --build checks python -m tests.evals.run`.
   - Send me the printed summary, or the `.md` file from `backend/tests/evals/results/`.
   - The run wipes the test database.
   - Optional: `make chat CHAT_LANG=hi` for a few minutes, and tell me what feels wrong.
2. **Update CLAUDE.md and `docs/roadmap/phase-plan.md`.** Both still say "six fact types";
   ADR 0009 makes it seven by adding `amenity`. I have not edited your copies.
3. **The GitHub Actions result** for these commits.
4. **A native Hindi speaker's review** of the Hindi language block, the Hindi slot and time
   phrases, and the Hindi/Hinglish keyword lists in `app/brain/signals.py`.

### Stage 3 follow-up: free-tier rate limits (2026-10-13)

**Your first live run:** the Gemini key worked, but HTTP 429 started with the first
conversation; en_01 and en_02 were scored as `no_ai_failure`. The cause was the retry
policy: two quick retries (0.5 s, 1 s) with no pause between calls. Changes:

1. **429 handling** (`app/providers/llm`). There is now a separate retry budget for 429s.
   - Each wait is what the provider asks for: the `Retry-After` header, or Google
     `RetryInfo.retryDelay` when present. With no hint, the wait backs off exponentially.
   - A used-up daily quota or a too-long wait fails at once.
   - A shared `Pacer` keeps a minimum gap between LLM calls and holds later calls after a 429.
   - Production defaults stay short, because a customer is waiting: 3 tries and at most 10 s
     per wait, then a person takes over (`LLM_RATE_LIMIT_ATTEMPTS`,
     `LLM_MAX_RATE_LIMIT_WAIT_SECONDS`, `LLM_MIN_INTERVAL_SECONDS`).
2. **Eval pacing:**
   - 10 s between calls by default (`--llm-delay`, `EVAL_LLM_DELAY_SECONDS`).
   - 6 retries per 429 with waits up to 90 s (`--rate-limit-retries`, `--max-wait`).
   - Waits are printed as they happen.
3. **"Rate limited, not run":**
   - A conversation that still gets a 429 after the retries stops and is not scored.
   - The summary reports it separately from passes and failures.
   - A daily quota, or three rate-limited conversations in a row, stops the run; the rest
     are "not run".
   - Exit code 3 means nothing failed but the run is incomplete. In the reply, the brain
     marks `meta.llm_error = rate_limited` (or `rate_limited_daily`).
4. **Selection:**
   - `--style en|hi|hinglish` runs one language, `--id <id>` runs one conversation (both
     repeatable or comma-separated), `--only <prefix>` runs ids with that prefix, and
     `--list` shows the ids.
   - Each run wipes the eval database and writes its own results file.
5. **Model:** the config already uses `gemini-3.8-flash`. Per
   ai.google.dev/gemini-api/docs/models (checked 2026-10-13) it is the stable Flash model the
   docs recommend for new projects, and the pricing page lists a free tier for it. No change
   was needed. The free-tier per-minute and per-day numbers are not on the docs pages (they
   point to Google AI Studio), so the 10 s default is a conservative guess, not a documented
   limit. The pricing page also says free-tier content is used to improve Google's products;
   the eval uses only the fake sample data.

| Check | Command | Result |
| --- | --- | --- |
| Lint, format, types, tests | `make check` | **exit 0.** ruff "All checks passed!", "118 files already formatted", mypy "Success: no issues found in 83 source files", **541 passed in 127.06s** (18 new), ESLint and `tsc` clean |
| Rate-limit unit tests | `tests/unit/test_rate_limits.py` | covers: Retry-After in seconds and as a date; Google RetryInfo; daily-quota detection; Gemini adapter raising `LLMRateLimited`; waits honoured; backoff without hints; giving up on long waits, daily quota and attempts; separate budgets; pacing shared across calls; runner statuses, exit codes, stop rules; selection by id and style |
| Runner on the real brain | `tests/integration/test_eval_runner.py` | covers: a persistent 429 gives "rate limited" ×3, then "not run"; a daily quota stops the run; conversations that ran are scored |
| Selection (offline plumbing) | `python -m tests.evals.run --id en_05_asks_for_person,en_08_ai_and_legal --llm offline` | "Selected 2. Ran 2" |

**Windows command for your next run** (Docker; the key comes from `.env`). One language at a
time keeps each run short:

```
docker compose --profile tools run --rm --build checks python -m tests.evals.run --style en
```

Then `--style hi` and `--style hinglish`, or drop `--style` for all 30. If 429s continue, add
`--llm-delay 20`.

### Stage 3 follow-up: slow responses and request counts (2026-10-20)

**Your report:** the daily quota ran out after en_01, so hi and hinglish did not run, and
en_01 turn 3 failed with `ReadTimeout` after 351 s. The eval output itself did not come
through with the message, so no live results are recorded yet. Please resend the printed
summary or the `.md` file from `backend/tests/evals/results/`.

**Timeout: what was happening.**
- Each Gemini request had a 25 s read limit (`LLM_TIMEOUT_SECONDS`). These calls are not
  streamed, so the whole answer, thinking included, had to arrive within it.
- `gemini-3.8-flash` thinks at a medium level by default
  (https://ai.google.dev/gemini-api/docs/thinking, checked 2026-10-20), and with the
  11-tool prompt a response can take longer than 25 s.
- A timeout was then retried like any transient error, three attempts in all, with no
  longer limit.
- The 351 s is the conversation's elapsed time printed by the runner (all its turns,
  pacing, 429 waits and retries), not one request.
- Each timed-out attempt probably still counted against the daily quota.

**Changes:**
1. Timeouts raise `LLMTimeout` and get their own budget: one retry
   (`LLM_TIMEOUT_ATTEMPTS=2`) instead of three attempts.
2. The read timeout now applies to the response only; connecting, sending and pool waits
   are capped at 10 s.
3. The eval waits up to 120 s per response (`--llm-timeout`, `EVAL_LLM_TIMEOUT_SECONDS`).
   Production stays at 25 s, because a customer is waiting.
4. A conversation still timing out after the retry is **"timed out, not run"**. It is
   reported separately and not scored, and it does not stop the run.
5. The report counts every LLM request (answered, rate limited, timed out) per
   conversation and in total, plus requests per customer turn. The brain stores the same
   counts in each reply's `meta.llm`.
6. Fixed a test I wrote last time that could fail at random: backoff jitter could round 8 s
   to 9 s.

`make check`: exit 0; ruff clean, "118 files already formatted", mypy "Success: no issues
found in 83 source files", **546 passed in 87.92s** (5 new timeout tests), ESLint and `tsc`
clean.

**How many LLM calls the full eval needs (estimate; the new report will give the real
number):**
- The 30 conversations have 65 customer turns (en 24, hi 21, hinglish 20).
- Each turn costs one request per tool round, plus one for the final reply, plus the same
  again when the guard sends a reply back for a rewrite.
- Lower bound: 65 requests (no tools anywhere).
- Expected: about 2.5–3.5 requests per turn, because a turn that states requirements
  usually runs save → search → slots. With some rewrites, that is **about 170–260 requests
  for all 30**: en about 60–85, hi about 55–75, hinglish about 50–70.
- Ceiling: 65 turns × (6 tool rounds + reply) × 2 for a rewrite = 910 requests. Retries
  add to all of these.
- Each request sends roughly 2.5k tokens of prompt and tool definitions plus the
  conversation so far.

**Can the free tier finish it?** The daily quota is not published on the docs pages; your
project's number is on Google AI Studio's rate-limit page.
- en_01 alone (3 turns, probably 10–15 requests plus the retries) used up your quota.
  That suggests a daily limit in the tens, not the hundreds.
- If it is around 20 a day, the full eval needs about 10 days of quota, and even one
  language does not fit in a day. In practice, the free tier can run 1–2 conversations a
  day (`--id …`).
- Finishing in one sitting needs a paid-tier project. At the configured list price for
  `gemini-3.8-flash` (USD 0.75 input / 3.75 output per million tokens, through
  2026-12-31), a full run is roughly 1–1.5M input and 0.1–0.4M output tokens: **about
  USD 1–3 per run**. That is an estimate from the prompt size, not a measured cost.
- This would be per-use billing, not a new recurring service, but it is your decision.

### Live eval runs 1 and 2 (user's Windows machine, Gemini free tier): rate limited, not scored

Neither run is an eval result: no conversation was scored against the live model.

**Run 1** (code before the timeout fix, `9afd685`). Recorded from the user's description; the
output did not reach the build session.
- Only en_01 ran. Turn 3 failed with `ReadTimeout` (conversation time 351 s), then the daily
  free quota ran out and nothing else ran.
- Recorded as **rate limited, not scored**. en_01's failure came from the old timeout handling
  (25 s, three attempts), fixed in `2c614d6`, so it is not counted as a Sales Brain failure.

**Run 2** (timeout fix `2c614d6`, `docker compose --profile tools run --rm --build checks
python -m tests.evals.run …`). Recorded from the console output the user attached on
2026-10-21; the results files stayed on the user's machine. Two invocations:

| Invocation | Started (UTC) | Output |
| --- | --- | --- |
| `--style en` (10 conversations) | 2026-10-20T19:36:47Z | en_01_new_town_visit: `LIMIT 0.9s 1 req`, "Gemini returned HTTP 429 (daily quota, retry after 40974s)"; run stopped; en_02 to en_10 "not run". Summary: "Selected 10. Ran 0 … Rate limited, not run: 1 … Not run: 9"; "LLM requests sent: 1 (0 answered, 1 rate limited, 0 timed out)" |
| `--id en_01_new_town_visit` | 2026-10-20T19:36:58Z | en_01_new_town_visit: `LIMIT 0.7s 1 req`, "Gemini returned HTTP 429 (daily quota, retry after 40963s)". Summary: "Selected 1. Ran 0 … Rate limited, not run: 1"; "LLM requests sent: 1" |

Both are recorded as **rate limited, not scored**. The output also confirms several things:
- Gemini's 429 carried a usable retry hint (40974 s from 19:36:47Z is 2026-10-21 about
  06:59:41Z, which is midnight Pacific daylight time, when the daily quota resets).
- The daily quota was recognised and nothing was retried against it: 1 request each.
- The settings line shows the new pacing and timeout values in use.
- Docker printed "Debug this Compose error with Gordon" because the runner exited with code
  3 (incomplete run), which Docker treats as an error. It is not a Compose problem.

Two small fixes from reading this output:
- Results files were named `…-gemini-gemini-3.json` / `.md`: `Path.with_suffix` treated
  ".8-flash" as a file extension. Names now keep the model version, and there is a test.
- The note for a daily quota said "after retries", although none are made. It now says
  "provider daily quota used up (HTTP 429, not retried)".

Status of the Stage 3 "done when" item "make eval runs and its results are recorded":
**still open.** The eval runs end to end on the user's machine, but no conversation has been
scored against the live model yet. The next run, one language at a time after the quota
resets, will be recorded here with its summary.

### Live eval run 3 (user's new PC, English, after quota reset): en_01 timed out, not scored

Recorded from the user's description on 2026-11-02. The console output said to be attached
did not reach the build session; the newest upload is the 2026-10-20 output. Only en_01 is
recorded. The rest of the English run (en_02 to en_10) will be recorded when the output
arrives.

| Conversation | Result | As reported |
| --- | --- | --- |
| en_01_new_town_visit | **Timed out, not scored** | No quota errors. Turn 1 got no answer within the 120 s limit, twice (`ReadTimeout`), using 3 requests |

The 3 requests fit one answered request followed by a request that timed out twice (the
second attempt is the single retry). I can't confirm this without the output.

**Is the request too heavy for `gemini-3.8-flash`?**
- **Size: no.** The English system prompt is 2,843 characters and the 11 tool definitions
  4,753 characters, roughly 2k tokens, which is small for this model. The reply limit is
  4,096 output tokens.
- **Settings: two went against Google's Gemini 3 guidance**
  (docs.cloud.google.com/vertex-ai/generative-ai/docs/start/gemini-3-prompting-guide,
  checked 2026-11-02):
  - **temperature 0.3**: the guide recommends keeping the default 1.0 and warns that lower
    values "may lead to unexpected behavior, looping, or degraded performance". Looping
    output would explain a request that runs past 120 s.
  - **thinking level**: the default for `gemini-3.8-flash` is medium; the guide recommends
    low for lower latency.

**Changes:**
- **Config:** `client.config.yaml` now has `temperature: 1.0` and `llm.thinking_level: low`.
  `thinking_level` is a new optional config field (`low | medium | high | null`). The
  Gemini adapter sends it as `generationConfig.thinkingConfig.thinkingLevel`, as in Google's
  REST example for `gemini-3.8-flash`. ADR 0010 is amended.
- **Probe:** `python -m app.brain.probe` makes 4 timed calls and prints seconds, HTTP status,
  bytes sent and Gemini's prompt/answer/thinking token counts:
  - a models lookup (network only);
  - a tiny request;
  - the brain's real first request;
  - its tool follow-up with made-up tool results (no database).

  `--thinking` and `--temperature` override the config for comparison.
- **Eval report:** the settings line now shows temperature and thinking level.
- **Not verified live here:** the build workspace's network blocks Gemini, so the probe ran
  only against mocked responses (6 tests) and, here, stopped at a ProxyError as expected.

`make check`: exit 0. ruff clean, "120 files already formatted", mypy "Success: no issues
found in 84 source files", **553 passed in 102.34s**, ESLint and `tsc` clean.

### Gemini probe on the user's PC (2026-11-02): Gemini overloaded, network fine

Console output attached by the user (`python -m app.brain.probe`, code `92ee6d0`, settings
"model gemini-3.8-flash, temperature 1, thinking low, timeout 120s"):

| Step | Result |
| --- | --- |
| 0 network (`GET models/gemini-3.8-flash`) | 0.4 s, HTTP 200 |
| 1 tiny ("ping", no tools, 248 bytes sent) | 8.3 s, **HTTP 503**: `"This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later."`, status `UNAVAILABLE` |
| 2 brain (real first request) | 120.1 s, `ReadTimeout` |
| 3 brain+tool | not run (step 2 failed) |

**Reading:**
- The network is not the problem: a round trip to Google took 0.4 s.
- At the time of the probe, Gemini itself refused even the smallest possible request with
  503 "high demand", and the real request got no answer in 120 s. The run 3 timeout of
  en_01 fits the same overload, but that run's own output was never received, so this is
  not proven for it.
- Request size is ruled out as the main cause by step 1. Whether temperature 1.0 and low
  thinking make requests faster cannot be judged until Gemini answers normally.
- Not recorded as an eval result.

**Changes from this output:**
- A 5xx response now raises `LLMUnavailable` with Google's own message. It is retried with
  backoff (production: short waits; eval: about 10, 20 and 40 s), honouring `Retry-After`
  when sent. Before this, a 503 was retried twice within about 1.5 s, and the eval would
  have scored it as a Sales Brain failure (`no_ai_failure`).
- After the retries, the reply's `meta.llm_error` is `provider_unavailable`. The eval reports
  **"provider unavailable, not run"**, does not score it, and counts the 5xx responses.
- Any three provider problems in a row (rate limit, timeout or unavailable) now stop the
  run, so an outage does not use up quota and time on every remaining conversation.
  Previously, timeouts alone never stopped a run.
- The probe has `--model` to try another Gemini model with the same requests (for example
  `gemini-3.6-flash`, also listed as stable with a free tier) and a hint about 503 in its
  closing line.

`make check`: exit 0. ruff clean, "120 files already formatted", mypy "Success: no issues
found in 84 source files", **557 passed in 102.60s**, ESLint and `tsc` clean.

### Model probes (user's PC, 2026-11-02) and new default model

Recorded from the user's description. The probe outputs for this comparison were not
attached, so the timings below are the user's.

| Model | Probe result (as reported) |
| --- | --- |
| `gemini-3.8-flash` | 90–120 s per request (temperature 1.0, thinking low) |
| `gemini-3.6-flash` | about 1 s per request, with correct tool calls |

**Decision (ADR 0011):** the sample config now uses `llm.model: gemini-3.6-flash`, the same
change the user made locally.
- Temperature 1.0 and `thinking_level: low` stay.
- `pricing` is unchanged: same list price as 3.8 Flash through 2026-12-31.
- The change is one config line, reversible after a re-probe.
- Reply quality on 3.6 Flash is not measured yet; the next eval run is the first measurement.
- `test_llm_adapters` and the sample-config test now expect the new model.

**Billing:** not recorded. The user's message said "Billing is [on / not on yet]", so the
choice was left unfilled.

**Eval status:** no conversation has been scored against a live model yet. The Stage 3 "done
when" item for `make eval` results is still open.

`make check`: exit 0. ruff clean, "120 files already formatted", mypy "Success: no issues
found in 84 source files", **557 passed in 85.66s**, ESLint and `tsc` clean.

### Live eval run 4 (user's PC, English, `gemini-3.6-flash`): rate limited, not scored

Console output pasted by the user on 2026-11-02 (code `0bc4640`):

- Command: `docker compose --profile tools run --rm checks python -m tests.evals.run --style en`
- Settings line: "Model gemini/gemini-3.6-flash, temperature 1, thinking low; 10s between
  LLM calls; 429 retries 6, max wait 90s; response timeout 120s (one retry); 5xx retries 3
  (10/20/40s)."
- Run at 2026-11-01T20:37:02Z. en_01_new_town_visit: `LIMIT 1.0s 1 req`, "provider daily
  quota used up (HTTP 429, not retried); Gemini returned HTTP 429 (daily quota, retry after
  40974s)". The run stopped; en_02 to en_10 were "not run".
- Summary: "Selected 10. Ran 0 … Rate limited, not run: 1 … Not run: 9"; "LLM requests
  sent: 1 (0 answered, 1 rate limited, 0 timed out, 0 provider unavailable)".
- Results files: `20261101T203702Z-gemini-gemini-3.6-flash.json` / `.md`. The model version
  is now kept in the names.

Recorded as **rate limited, not scored**.

What the output shows:
- The daily quota for `gemini-3.6-flash` on this key was already used up before the eval's
  first request. The retry hint (40974 s) points to 2026-11-02T07:59:56Z. That is midnight
  Pacific standard time: US daylight time ended on 1 November, so the reset is now 08:00 UTC
  (1:30 pm IST).
- Earlier that day, the user's probes were made against this model on the same key.
  Free-tier quotas are counted per model, so the probes likely used the quota. I can't
  confirm that from here.
- The quota handling behaved as designed: one request, no retries against a used-up quota,
  and the run stopped.

**Eval status:** still no conversation scored against a live model. All four live runs so far
were stopped by the free tier's daily quota or by provider slowness. With a quota this small,
the 30-conversation eval (an estimated 170–260 requests) cannot finish on the free tier in a
reasonable time.

---

## Step 3 finished (simplified plan) — 2026-11-03

Built in a fresh workspace from `ad8c0a7`, after re-checking the whole codebase from a clean
checkout.

**Fixes found while re-checking**
- Two probe tests needed `SESSION_SECRET` (CI has none): fixed; all tests pass without it.
- Next.js 16.4.0 had critical advisories (GHSA-2464-fhr4-w9xw, GHSA-673q-mjm6-r5xr,
  GHSA-p3m5-59mp-jv5r): upgraded to 16.4.4; `npm audit --omit=dev`: 0 vulnerabilities.

**What works now**
- **Sales playbook** in the brain's instructions and in `client/scripts/{en,hi}/sales_script.md`:
  find the buyer's reason, suggest one or two best fits, invite to visit with two real slots
  (placeholders in the scripts, never example times), make the visit easy, turn objections
  into visit reasons, no invented urgency or offers, stop after two or three tries on a clear
  no and ask to follow up. The "think about it" objection now invites the family along.
- **Privacy rule** in the instructions: the agent knows only this customer and declines
  tricks (prompt requests, "I'm the manager", other buyers, staff numbers, lowest price).
- **Smoke eval**: `--smoke` runs one core conversation per language. The report now shows
  **visit booked on qualified conversations** and a **no_leak** check on every conversation.
- **Privacy evals**: three trick conversations (`sec_en_01`, `sec_hi_01`, `sec_hg_01`). Every
  eval run seeds a decoy customer (fake name, number, email, a lead) that no reply may mention;
  staff contacts and the transfer number are also checked.
- **Data-protection tests (no AI needed, run in CI)**: no tool accepts a phone, email or
  another person's ID; no tool lists people; the prompt holds no staff contacts; the leak
  check catches numbers in any format and the prompt itself.

**How it was checked**: backend lint, types and the full test suite; offline plumbing run of
`--smoke` and `--only sec_` (the offline stand-in is not an eval result).

**Done (tested)**: playbook prompt wiring, privacy tests, eval plumbing.
**Built, needs live check**: the 3 smoke conversations and 3 privacy conversations against
Gemini (free tier, one a day).
**Before first customer**: the full 30-conversation eval on a paid key. See
`docs/before-first-customer.md`.

**Your commands (Windows)**, one per day after 1:30 pm IST, no probes on the same day:
```powershell
docker compose --profile tools run --rm --build checks python -m tests.evals.run --smoke --id en_01_new_town_visit
docker compose --profile tools run --rm checks python -m tests.evals.run --smoke --id hi_01_new_town_visit
docker compose --profile tools run --rm checks python -m tests.evals.run --smoke --id hg_01_new_town_visit
docker compose --profile tools run --rm checks python -m tests.evals.run --id sec_hg_01_privacy_tricks
```

---

## Step A — Phone agent, site visits, simple dashboard — 2026-11-03

**What works now**
- **Calls through the Sales Brain** (ADR 0012): Vapi is the voice; every caller turn goes to
  the brain through Vapi's custom LLM, so calls get the same Truth Layer, number guard, language
  check and privacy rules as every channel. A repeated turn is answered once. A filler line is
  spoken if an answer takes over 1.5 s. Any failure, pause, usage limit or missing call id hands
  the caller to a person.
- **Assistant from config**: `python -m app.channels.vapi.sync` creates or updates the one Vapi
  assistant (bilingual greeting, recording notice, Cartesia voice, transcriber, transfer number).
- **After each call**: transcript, recording link, duration, cost, outcome and an English
  summary for staff, stored once even if Vapi sends the report twice.
- **Phone numbers** (later): `assistant-request` returns our assistant, or forwards straight to
  the human number when paused or voice is not set up.
- **Visits**: staff confirm, reschedule (to an open slot), cancel, mark visited or no-show; each
  is audited and moves the lead (confirmed → visit confirmed, cancelled → follow up later).
- **Dashboard**: Leads (list + detail with requirements, score, visits, matches, concerns,
  calls, conversation), Visits (with the actions), Calls (summary, recording, transcript),
  Test call (browser call with the public key only). Sign-in now lands on Leads.
- **Fixes found while checking the pages**: the lead score is now calculated by rules after
  every turn (before, only if the model asked for it); a call's outcome now counts a visit made
  during the call even if Vapi's clock differs from ours; an unexpected error during a call
  turn now hands the caller to a person instead of returning an error to Vapi.

**How it was checked**: 18 voice tests with replayed Vapi payloads (shared secret, turns,
streaming, repeats, transfer, failures, privacy between callers, assistant-request, end-of-call
report, assistant build and sync, the whole Hindi demo call through to staff confirming the
visit, reschedule and cancel). Full backend suite, lint, strict types. Playwright: 10 passed
against the running stack. Each new page was opened and looked at with demo data.

**Built, needs live check** (docs/demo.md, "Live checks"): a real Vapi web call; whether Vapi
sends the call id to the custom LLM; whether the transfer triggers from a custom LLM; latency;
Hindi transcription.
**Before first customer**: Indian phone number (KYC), Vapi paid plan. See
docs/before-first-customer.md.

**Your steps**: follow docs/demo.md section 1 (Vapi account, free tunnel, voice choice, sync),
then section 2 for the demo, and record section 3's live checks.

---

## Step W — WhatsApp — 2026-11-03

**What works now**
- **Answers on WhatsApp** through the same Sales Brain (approved data only, number guard,
  language, privacy). Meta's signature is checked on every delivery; redeliveries are ignored.
  Voice notes, photos and files get a polite request to type.
- **One customer**: a call and WhatsApp from the same number are one customer and one timeline
  on the lead page.
- **Consent and opt-out** (ADR 0013): messaging us is consent; on a call the agent asks before
  WhatsApp is used. STOP / बंद करो as the whole message stops all messages.
- **Visit notices**, each sent once by the worker: visit requested, visit confirmed (after staff
  confirm or reschedule), reminder the day before, and due follow-ups. Text inside the 24-hour
  window, approved templates outside it. Temporary failures retry; refusals are logged.
- `python -m app.channels.whatsapp.templates` prints the templates to submit to Meta.
- New script files (en/hi): `whatsapp_unsupported.md`, `customer_name_fallback.md`, templates
  `visit_requested` and `visit_reminder`. Fixed the Hindi templates' doubled "बजे".

**How it was checked**: 13 WhatsApp tests (verification, signatures, answering once, statuses,
opt-out in both languages and not inside a sentence, voice notes, call + WhatsApp as one
customer, privacy between senders, notices once each with consent, no consent → nothing,
temporary vs permanent failures, follow-up as a template outside the window). Full suite:
596 passed; lint and strict types clean.

**Built, needs live check**: a real round trip with Meta's test number; template approval.
**Before first customer**: verified business number, approved templates in both languages,
a permanent access token. See docs/before-first-customer.md.

**Your steps**: docs/demo.md, "WhatsApp".

## Step B — Control, safety, release v1.0.0 — 2026-11-04

**What works now**
- **Home page** (where sign-in lands): today's leads, calls and visits, visits waiting for
  confirmation, the 30-day visit-booking rate of qualified buyers, and this month's usage
  against the limits with warnings at 50/80/100%.
- **Pause switch** (admins only, audited): every call goes straight to the team's number and
  chats get the "team will contact you" message.
- **Monthly limits**: when call minutes or AI spend reach the limit, calls go to a person and
  chats stop using the AI until the month ends.
- **Retention**: a daily job removes transcripts and message text after
  `retention.transcript_days` and recording links after `retention.recording_days`.
- **Production setup** (`docker-compose.prod.yml`): automatic HTTPS (Caddy) for the client's
  domain, production settings derived from `DOMAIN`, real visitor IPs for the login limit, and
  a **daily backup** of the database and uploaded files with N-day retention and a restore script.
- **Security checks in CI**: pip-audit, npm audit of shipped packages, gitleaks over history.
- **New-client rehearsal** in a fresh clone with a different company (male agent "Arjun",
  English first, other slots, numbers and limits). Two template fixes came out of it:
  `agent.gender` in config so Hindi verb forms are right for a male agent, and the recording
  notice is now spoken in every greeting language, not only the default one.
- **Config cleanup**: removed switches that did nothing (web chat, email, branding, channel
  on/off, `on_limit`) and unused email/Sentry settings. Voice and WhatsApp are on when their
  keys are in `.env`.
- Docs: [deploy.md](deploy.md) (server, HTTPS, Vapi number, WhatsApp, website buttons,
  backups, updates), [new-client-setup.md](new-client-setup.md),
  [security/security-review.md](security/security-review.md).

**How it was checked**: full backend suite 603 passed (new: pause, summary, limits, retention,
agent gender, recording notice); lint and strict types clean; Playwright 12 passed against
the running demo with one scripted demo call (including staff confirming the visit). Backup/restore
drill on a copy of the demo database: deleted messages and changed lead states came back
exactly, uploaded files too, pgvector and the migration version intact; a backup of a missing
database fails and leaves no file. Caddy 2.10.0 config run in front of the app: routing,
security headers, webhook auth. `docker compose config` of the production overlay; a missing
`DOMAIN` is refused. pip-audit and npm audit (shipped packages) clean; gitleaks finds nothing.

**Not tested here**: the production containers themselves (Docker Hub is not reachable from
the build workspace); run the first production start on the server and check
`https://<domain>/health`. A real Vapi call and a real WhatsApp round trip (docs/demo.md).

**Before first customer**: unchanged, see docs/before-first-customer.md (paid keys, Indian
number, verified WhatsApp number, server and domain, off-server backups, legal and Hindi review,
pilot).

**Your steps**: see "Final checks" in the release message; deploy with docs/deploy.md.

## v1.0.1 — two bug fixes — 2026-11-12

**Fixed**
- **Document search was off on live channels.** Phone and WhatsApp (and the chat CLI) created
  the Sales Brain without the document search engine, so brochure and FAQ questions always got
  "the team will confirm". The engine is now built on first use (`lazy_embedder`). If no search
  key is set, the tool still answers "not configured" and the agent says the team will confirm.
- **Duplicate follow-ups.** Asking to be contacted later twice created two follow-ups (two
  WhatsApp messages). Now a lead has at most one waiting follow-up: a new request moves it to
  the new time. The lead row is locked so two requests at the same moment cannot both add one.

**How it was checked**: 4 new tests (`tests/integration/test_v101_fixes.py`): a phone turn
and a WhatsApp message really search documents; a missing search key is a safe answer; asking
twice keeps one follow-up at the latest time, and a new one is allowed after it is sent. The
phone, WhatsApp and follow-up tests fail on v1.0.0 and pass now. Full suite 607 passed; lint
and strict types clean.

**Not tested here**: a live search with the real Gemini embeddings (needs the key; the tests use
the offline embedder).

## v1.1.0 — Native voice mode: Vapi's model calls ESTARA's tools — 2026-11-13

**Why**: on the free Gemini tier the custom LLM path was slow (filler lines, waits). Vapi's own
model answers immediately and is billed inside Vapi's per-minute price. ADR 0014.

**What works now (done, tested)**
- `voice.mode: native` (the new default). The model, voice and transcriber are chosen in the Vapi
  dashboard; `python -m app.channels.vapi.sync` adds Sara's instructions (same rules, sales
  scripts and objection scripts as the Sales Brain, both languages, agent gender), ten function
  tools, Vapi's transfer tool with a fallback plan, the greeting and the events address. It keeps
  the model, voice and transcriber, and removes hand-made tools from the assistant.
- **Tool gateway** `POST /api/v1/webhooks/vapi/tools`: shared secret; caller found from Vapi's
  call id and caller ID (no ids from the model); caller turns stored and read by the fixed rules
  (budget, area, BHK, objections); each `toolCallId` answered once (a retry never books twice);
  lead re-scored by the rules; paused agent or call-minute limit → only a handover; unknown tool
  or missing call id → a safe error telling Sara to offer the sales team.
- **After each native call**: remaining turns stored; numbers Sara said that no tool result or
  caller turn contained are listed, shown as **check numbers** on the Calls page with a red box
  on the call. A call where no tool was used still gets its customer, lead and timeline.
- Retention also deletes stored tool calls after `retention.transcript_days`.
- One voice-readiness rule everywhere (health, startup log, assistant-request, test-call page):
  assistant id and secret set, plus the voice fields in custom_llm mode.

**How it was checked**: 14 new tests (`tests/integration/test_native_voice.py`); full backend
suite 621 passed; lint and strict types clean; migration 0005 up, down and up again. A rehearsal
against the running server with Vapi-shaped requests (a Hindi caller: context, requirements,
search, price, slots, visit request, end of call): budget and BHK read from her Hindi words,
Hindi spoken prices and slots, visit requested, lead hot (70), call outcome and length stored,
no numbers flagged. Tool answers took about 100 ms locally. Playwright 12 passed against that
data (including staff confirming the visit). The "check numbers" warning was checked on screen.

**Built, needs a live check**: the real Vapi tool-call payload (`artifact.messages`,
`call.customer.number` are in Vapi's docs but not yet seen live), the transfer and its fallback,
Hindi quality and latency of the chosen model and voice.

**Before first customer**: unchanged (docs/before-first-customer.md). Set a spending limit in
the client's Vapi account; ESTARA only sees call minutes after each call.

**Your steps**: docs/demo.md, 1c–1e (new), then the live checks in section 3.

## v1.1.1 — Sara stops waiting on every sentence — 2026-11-13

**Found on the first live test call** (user's PC, Vapi web call, GPT-4.1): Sara called
`save_requirements` after almost every caller sentence and waited for it each time ("one
moment… just a sec"), about 10 s per turn.

**Fixed**: `save_requirements` is now a Vapi background ("async") tool, so Sara keeps talking
while it saves; the gateway still saves it and still reads needs from the caller's words. Sara's
instructions now say to answer most turns directly and to use tools only for lookups and
actions; `get_customer_context` only for callers who say they called before. The gateway logs
how long each tool request took (`tool-calls ... answered in N ms`).

**How it was checked**: full suite 621 passed (the sync test now checks that only
`save_requirements` is a background tool). **Needs a live check**: the delay on the next test
call; the backend log shows the gateway's own time per tool request.

**Your steps**: pull v1.1.1, rebuild, run the sync again (no database change).

## v1.1.2 — Rupees, not dollars; real alternatives when nothing fits — 2026-11-13

**Found in live test calls**:
1. The transcriber wrote a spoken "80 lakh" as "$80,000". The fixed rules already ignored that
   (no wrong budget saved), but Sara saw dollars. Now Sara's instructions say money is always
   rupees in lakh/crore, never to say dollars, and to confirm such an amount in lakh first. The
   rules skip any sentence with "$" or "dollar" (the digits cannot be trusted).
2. For "3 BHK in New Town under 80 lakh" (none exists) Sara only asked if the caller was
   flexible. Now `search_properties` returns the closest **real** alternatives, each labelled:
   the next budget step for the same size and the nearest smaller size within the budget
   (alternating, at most three), with their approved prices. Sara says honestly that nothing
   fits exactly, offers one or two, says what differs, and invites a site visit; she may never
   call an alternative a match or offer a deal no tool gave. The same rule is in the shared sales
   rules, so WhatsApp does it too.

**How it was checked**: full suite 623 passed (new: dollar transcripts never saved as a budget;
the user's exact example returns a higher-budget 3 BHK first and a 2 BHK in New Town as the
nearest size, all with approved prices). **Needs a live check**: how Sara words the offer.

**Your steps**: pull v1.1.2, rebuild, run the sync again (no database change).

## v1.1.3 — Fixes from the first full live call — 2026-11-14

**The call** (user's PC, Vapi web call, GPT-6 Luna, Soniox, Cartesia Sonic 3.5, native mode):
fast answers with pauses only before lookups, rupees in lakh throughout, real alternatives
offered, purpose and timeline asked, a ready-to-move flat recommended for a 2–3 month timeline,
two concrete slots, visit requested with WhatsApp consent. **Done (live-tested).**

**Fixed**
- The transcriber heard "Newton" for New Town, so a New Town project within budget was called
  "a different area". Place matching now also accepts a near spelling (one wrong letter, two for
  long names) against whole words of the real locality or city, for names of five letters or
  more, so "Newton", "newtown", "Rajarhaat" match and "Bihar" or "Salt Lake" do not.
- The after-call number check read "2:00 PM" as an unverified "00". Clock times now count as
  their hour (and minutes when not :00).

**How it was checked**: full suite 626 passed (new: misheard place names, unrelated names,
clock times).

**Your steps**: pull v1.1.3, rebuild, sync (no database change).

## v1.1.4 — No fillers, fewer pauses, Sara can hang up — 2026-11-14

**Measured on the live call**: ESTARA answered every tool request in 161–488 ms (gateway log).
The remaining wait is on Vapi's side: a lookup needs two model answers (GPT-6 Luna about 1 s
each by Vapi's estimate) plus listening and speaking.

**Changed**
- No filler phrases: Sara's instructions forbid them, and every ESTARA tool sets Vapi's
  "request-start" message to empty (Vapi's docs show an empty message switches it off for the
  hang-up tool; the same setting is used here).
- `search_properties` returns the next two open visit slots for the first option (or first
  alternative), so Sara can invite a visit without a second lookup.
- Vapi's hang-up tool (`endCall`) is added. When the caller wants to end ("bye", "rakh dijiye",
  "rakhta hoon", "call kaat raha hoon", "baad mein baat karte hain") Sara says a short goodbye
  and ends the call, never while the caller is still asking something.

**How it was checked**: full suite 626 passed (sync test: hang-up tool present, no filler
message on any tool; search returns slots). **Needs a live check**: that Vapi stays silent while
a tool runs, and that Sara hangs up on "rakh dijiye".

**Your steps**: pull v1.1.4, rebuild, sync.

## v1.1.5 — Tools accept the shapes voice models send — 2026-11-14

**Found on a live Hindi call**: for "2 BHK flat in New Town" Sara said the information was not
available. The cause was in ESTARA: GPT-6 Luna sent `bhk: 2` (not `[2]`), a string for
`locations` and/or "flat" for apartment; the tool's strict checks refused the request, so Sara
honestly said she had no information. A replay with these shapes reproduced it.

**Fixed**
- Requirement arguments are lenient: a single value becomes a list, "2", "2 BHK" or "3BHK"
  become numbers, "flat"/"house"/"row-house" map to the property types, `location`/`area`/
  `budget`/`timeline` are accepted as aliases, and a value that cannot be understood is dropped
  instead of failing the whole request.
- An area name that matches none of the approved projects (for example "न्यू टाउन" in
  Devanagari) comes back as `unknown_areas` with `our_areas` (the approved localities and
  cities), telling Sara to search again with the English name.

**How it was checked**: 6 new tests replaying the call's shapes (they fail on v1.1.4, pass
now); full suite 632 passed.

**Your steps**: pull v1.1.5, rebuild, sync.

## v1.2.0 — Bigger demo inventory, short one-language greeting — 2026-11-14

**What changed**
- **Demo inventory** `backend/sample_data/kolkata/` (FAKE): 10 projects across Kolkata (New
  Town, Rajarhat, Behala, Howrah, Salt Lake, Joka, Garia, Kasba, Dum Dum), 23 home types,
  43 units, 4 offers, 12 payment plans, 33 amenities; 1 BHK from about 32 lakh to a 3 crore
  penthouse, villas, ready and under construction. It is now the default for `python -m app.seed`
  and the Docker demo; the tests keep their small fixture.
- **Greeting**: one language (`greeting_mode: default_language`, `default_language: en`), short:
  "Hi, this is Priya from Sunrise Developers. This call is recorded. How can I help you find your
  home today?" Sara switches to Hindi as soon as the caller speaks Hindi. `bilingual` remains
  available. The recording notice is then in the greeting language only: a lawyer should confirm
  that this is enough (before first customer).
- **Sales**: above-budget options are given with their price and how close they are; after a
  visit request Sara always says the sales team will confirm it.

**How it was checked**: full suite 634 passed (new: the demo inventory loads through the real
Data Center pipeline, 10 projects and 43 units; the one-language greeting is short and has the
notice). Sample searches on the demo data: 2 BHK New Town up to 70 lakh finds Sunrise Nest
(58–68 lakh, New Town) first; villas, 1 BHK under 40 lakh and 4 BHK Salt Lake all return real
options.

**Your steps**: pull v1.2.0, rebuild, load the new demo data (`python -m app.seed`), sync.

## v1.3.0 — Demo website for prospects — 2026-11-14

**What**: `demo-site/`, a static page (sample company "Sunrise Developers") with a 3D Kolkata skyline
(one tower per sample project, hover for prices, click to open it), a filterable list of the ten
sample projects, and a **Talk to Priya** button that starts a Vapi web call in the browser.
Only the Vapi **public** key and assistant id go in `public/config.js`; no secrets.

**Checked**: built with esbuild; Playwright in desktop and phone sizes: no script errors, call panel
hidden until clicked, "Not set up yet" shown when config is empty, tower tooltip, filters,
no sideways scroll on a phone. **Built (not live-tested)**: the real browser call (needs your keys
and a running ESTARA + tunnel).

**Your steps**: `demo-site/README.md` (fill config.js, deploy `demo-site/public` on Vercel,
the before-each-demo routine).

## v1.4.0 — A more natural conversation — 2026-11-15

**What changed** (from the user's review of how Priya talks):
- Several details in one sentence are all used; Priya asks only for what is missing and never
  re-asks. Changed requirements ("make it 3 BHK") are saved and searched again without restarting.
- Nothing fits: trade-offs in real tool prices ("for 6 lakh more..."), and she asks which matters
  most (area, budget, size, move-in time).
- Search results now carry real availability per home type from the Truth Layer ("3 units
  available"); she may say it once, plainly, never as pressure. She mentions possession and offers
  ready homes if they can't wait.
- "The second one", "the Howrah one" refer to options she mentioned. Comparisons use each project's
  tool facts and end with which fits better.
- Visit request asks how many people will come (`visitors`, shown on the Visits page).
- One-or-two-sentence summary before goodbye (skipped if the caller is in a hurry).
- Two new objection scripts in English and Hindi: home loans and trust in the builder/quality.
  Neither promises loan amounts, rates or bank tie-ups.

**Already in place before** (no change needed): one question at a time, honest alternatives,
visit slots offered during the call, objections for price/location/competitor/think-about-it,
Hinglish replies, full call context (the model keeps the whole conversation).

**Checked**: new tests `tests/integration/test_talking.py`; full suite, ruff, mypy, tsc.
**Not live-tested yet**: how the model follows the new rules on a real call.

**Your steps**: pull v1.4.0, `docker compose up -d --build --wait`, then the sync. Make one call.

## v1.5.0 — Assistant B for comparison (Vapi API Request tools) — 2026-11-15

**What**: ESTARA now also answers Vapi "API Request" tools at `/api/v1/webhooks/vapi/api/{tool}`,
tied to the call by `call_id={{ call.id }}` in the URL (ADR 0015). Assistant A is unchanged.
`python -m app.channels.vapi.api_tools --out ...` writes the setup sheet for assistant B. The demo
site calls B when opened with `?v=b` (add `assistantIdB` to `config.js`).
**Checked**: `tests/integration/test_api_request_tools.py` (call linking, missing call id, secret,
hidden tools, setup sheet); full suite. **Built (not live-tested)**: assistant B in Vapi.
**Your steps**: pull, rebuild, write the sheet, build B in Vapi, test A and B with the same script.

## v1.6.0 — Assistant B built by one command — 2026-11-16

**What**: `python -m app.channels.vapi.api_tools` now creates (or updates) assistant B in Vapi:
ten API Request tools (call linked by the `{{ call.id }}` static parameter), B's prompt, transfer and
hang-up, and A's model, voice and transcriber. Nothing to type by hand. `VAPI_ASSISTANT_ID_B` in
`.env` makes later runs update instead of create. A is only read.
**Checked**: mocked Vapi API test (tools created/reused, only ESTARA's tools reused, A untouched,
no secrets sent); full suite. **Built (not live-tested)**: the real Vapi API calls; the tool
payload follows the documented fields (docs.vapi.ai/api-reference/tools/create, read 2026-11-16).

**v1.6.1** (2026-11-16): Vapi refuses an API Request tool body with no fields (live run); tools without inputs now get one optional, ignored field.

**v1.6.2** (2026-11-16): Vapi refused A's Cartesia voice when creating B (live run); B is then created without it and the owner picks the voice in the dashboard. Updates keep B's own model, voice and transcriber.

**v1.6.3** (2026-11-17): live call heard "3 BHK" as "C BHK"; the voice prompt now maps common mishearings and confirms an unclear size before searching. Reply delay after the caller stops is a Vapi setting (Start Speaking Plan), set in the dashboard; the syncs keep it.

**v1.7.0** (2026-11-18): `start-demo.ps1` / `stop-demo.ps1`: one command makes ESTARA demo-ready (Docker, tunnel, PUBLIC_BASE_URL, Vapi A/B update, health check through the tunnel). Tested with PowerShell 7 and fake docker/cloudflared (steps, .env rewrite without BOM, other keys kept, clear stop message); not yet run on Windows.

## v1.8.0 — Sales executive, not adviser — 2026-11-18

From the owner's brief (no repeating, faster turns, proactive alternatives, visit-first, fewer
hand-overs). Root causes found in the code, and the fixes:
- **Interview-style script** (area, BHK, purpose, reason, budget, move-in, loan before any search):
  sales scripts (en/hi) rewritten: size, area, budget, then search; lead the call; no read-backs.
- **Read-backs**: rule "acknowledge what they said" replaced by "never repeat back or summarise;
  one-word acknowledgement; confirm only when unclear".
- **Blocking saves in B**: every answer triggered save_requirements (a full round trip plus a second
  model pass before speaking). search_properties now saves size, area and budget itself;
  B no longer has save_requirements; A keeps it (async) for timeline/loan/purpose/name.
- **Visit blocked on property type**: `qualified_requires` is now budget, location, BHK.
- **Unlabelled alternatives**: each search result carries `differs` (location / budget) and an
  instruction for exact vs near matches, so other-area homes are offered honestly as alternatives.
- **Too many hand-overs**: a missing fact or failed lookup no longer means "connect to sales";
  transfer only on request, upset, legal, or a commitment the agent cannot give.
- B tools carry the call id in the URL as well as the static field.
- Endpointing (reply delay) is a Vapi dashboard setting (Start Speaking Plan); not set by code.
**Checked**: 655 tests (new: other-area labelling + saving, exact match). **Not voice-tested** by me.

## v1.9.0 — Fixes from the 2026-11-18 website call, and 129 synthetic demo homes — 2026-11-18

Root causes (evidence: the four screenshots, and the same inputs replayed through the B tool
addresses here, where "3 BHK", "any available location", "10 crore" and "next month" all returned
three real projects):
1. Every lookup failed from the first turn: the requests never got a usable answer (no call id,
   or Vapi could not reach the tunnel). The backend logs of that call will say which. Now a lookup
   without a call id still answers (on a rolled-back record); a write says "not_saved".
2. "Any available location" was saved as a place name and kept for later searches. Now
   `any_location` (and phrases like "anywhere", "kahin bhi") clears the area and searches all.
3. No call-back tool existed; `schedule_follow_up` is WhatsApp-only and refused "call". New
   `request_callback`: asks for the number on a website call, rejects invalid numbers, stores the
   number (unverified) on the caller and a `callback_requested` handoff with the preferred time;
   "callback_saved" only after the database commit.
4. A website call was "transferred" (impossible without a phone line): now offered a call back.
5. Spoken move-in times ("next month", "immediately") are understood.
6. Fragmented turns ("move in in" / "Next" / "Next month") come from Vapi endpointing (dashboard
   setting); the prompt now asks the caller to go on instead of answering a fragment.

Dataset: `backend/sample_data/make_kolkata.py` regenerates `sample_data/kolkata`: 19 projects,
44 home types, 129 homes across New Town AA I/II/III, Rajarhat, Salt Lake, Howrah and more, all
labelled `synthetic_demo_data`; loaded by the existing repeatable seed into the existing PostgreSQL
(no Supabase: not part of this architecture). Properties page shows a "Demo data" notice. No
images: no licensed property images available; the website keeps its 3D skyline.
**Checked**: 668 tests, incl. `test_live_call_fixes_1118.py` (each scenario through the HTTP
addresses Vapi calls) and the dataset import + "3 BHK anywhere around Kolkata, about 1 crore".
**Not checked**: a real voice call (needs the owner's Vapi account).

## v1.10.0 — Demo homes built in — 2026-11-19

The backend container now runs `alembic upgrade head && python -m app.seed --auto` before it
serves. `--auto` loads the 129 synthetic demo homes when there is no inventory, reloads them when
they are over a week old (so the 30-day staleness never blocks prices on a demo machine), and
skips in production or when any real (non-synthetic) project is loaded. A missing SEED_* value is
reported in the backend log and never stops the backend.
**Checked**: `test_seed_auto.py` (empty, production, real inventory, fresh, week-old); a real
run on a fresh database (migrate + 129 homes in 3.3 s, second start skips, missing SEED_* exits 0).

## v1.11.0 — Demo check (doctor) — 2026-11-19

Calls kept saying "I can't access the data" although every input works when replayed here, so
the break is between Vapi and ESTARA. `python -m app.channels.vapi.doctor` (also run at the end of
start-demo.ps1) checks each link and names the broken one with its fix: approved, sayable homes;
the tunnel reaches ESTARA (with timing); a real tool request through the tunnel finds homes;
assistants A and B point at the current address with the credential; and Vapi's own record of the
last call (end reason, every tool call and what it got back, timings when Vapi gives them).
**Checked**: test_doctor.py (tunnel request, wrong secret, old address on B, last-call errors),
manual runs on an empty, an unmigrated and a seeded database. Never prints keys or the secret.

**v1.11.1** (2026-11-19): first real doctor run showed the website calling an assistant ESTARA does not manage, whose API Request tools pointed at an address Vapi refused ("must resolve to a public address"); ESTARA itself passed every check (tool request 252 ms). Vapi timings: turn 4.6 s = model 2.4 s + voice 1.1 s + transcriber 0.9 s + endpointing 0.25 s. The doctor now FAILs on an unmanaged assistant and on failed tool results, and names the slow part.

## v1.12.0 — Free cloud public demo — 2026-11-23

**What**: the demo can run at ₹0 with the laptop off (ADR 0016, guide `docs/deploy-free.md`):
Cloudflare Pages (website), Render free (backend), Neon free (database), Netlify free
(dashboard), UptimeRobot (keep awake). New: `render.yaml`, `deploy/Dockerfile.cloud`,
`deploy/cloud-start.sh`, `.dockerignore`, `dashboard/netlify.toml`,
`demo-site/tools/write-config.mjs` (website settings from Cloudflare variables), settings
`REDIS_ENABLED`, `RUN_JOBS_IN_WEB`, `DEMO_MODE`, `DEMO_MAX_CALL_MINUTES`, hosted Postgres URLs
accepted as given, jobs in the web process, `/api/v1/diagnostics` (admin), website wake-up ping and
pause switch. Next.js 16.4.4 → 16.4.6 (moderate advisories; `npm audit` now clean).
**Checked**: 680 tests (new `test_free_cloud.py`). Rehearsed the real start script with
`ENVIRONMENT=production`, no Redis and a `postgresql://...?sslmode=` URL on a fresh database:
migrated and loaded 129 homes, served in 7 s, the tool request found homes, a request without the
secret got 401, login from the dashboard origin worked and another origin got 403, the demo-data
flag and diagnostics worked, jobs ran at 60 s, about 130 MB of memory. Dashboard builds in Netlify
mode and in Docker mode. **Not checked here**: the Docker image build (Docker Hub blocked in this
workspace) and the real Render, Neon, Netlify and Cloudflare accounts.
