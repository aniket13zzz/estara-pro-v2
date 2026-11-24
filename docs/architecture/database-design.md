# Database design

PostgreSQL 16 with the `vector` extension (pgvector 0.8). Single tenant: no `tenant_id`, no
row-level security. Models: `backend/app/db/models.py`. Migrations: `backend/alembic/versions/`
(Alembic only; `alembic check` must report no drift, enforced by a test).

## Conventions

- Primary keys are UUIDs (`gen_random_uuid()`), except `agent_state` (single row, `id = 1`).
- Timestamps are `timestamptz`. Money is whole rupees in `BIGINT`. Areas are square feet in
  `NUMERIC(10,2)`.
- Enumerations are `VARCHAR` with named `CHECK` constraints (`ck_<table>_<name>`), not native
  PG enums, so adding a value is a simple migration.
- Languages are stored as short codes (`VARCHAR(8)`) and validated against config, not the DB.
- JSON uses `JSONB`.

## Tables

| Area | Table | Notes |
| --- | --- | --- |
| Auth | `users` | Unique on `lower(email)`. Role `client_admin`/`sales_staff`. Lockout fields `failed_login_count`, `locked_until` |
| | `sessions` | Server-side sessions; only HMAC digests of the session and CSRF tokens are stored |
| | `audit_log` | Actor, action, entity, JSON details, IP. Indexed by entity and time |
| Data Center | `uploads` | File metadata and SHA-256 |
| | `import_batches` | One per uploaded entity file; status uploaded → validated → approved/rejected |
| | `import_rows`, `validation_errors` | Row data and row/column-level errors per batch |
| Inventory (Truth) | `projects`, `towers`, `unit_types`, `units`, `price_lists`, `offers`, `payment_plans`, `amenities` | See "Commercial columns" |
| Knowledge | `documents`, `document_chunks` | `embedding vector(768)` with an HNSW cosine index. Documents only |
| Customers | `customers` | `preferred_language`, memory JSON, WhatsApp opt-out, `possible_duplicate_of` flag |
| | `customer_identities` | (`identity_type`, `value`) unique **among verified rows** (partial index, migration 0003), so an unverified claim on someone else's number is stored and flagged, never merged; values normalised (E.164, lowercase email); `verified` |
| Conversations | `conversations` | `channel`, `language`, English summary |
| | `messages` | `channel`, `direction`, `sender`, `language`, `language_switched`, provider message ID (unique per channel when present); `tool_calls` (name, arguments, result per call); `meta` JSONB (inbound: detected signals and fields extracted by rules; outbound: guard results, fallback, handoffs, LLM provider/model) |
| | `calls` | `vapi_call_id` unique, recording URL, transcript, English summary, duration, outcome, cost |
| Leads | `leads` | `state`, `score` (0–100), `band`, `score_reason`. Index (`state`, `score`) |
| | `lead_requirements` | One row per lead: budget min/max (INR), locations, property type, BHK list, timeline, loan, purpose |
| | `lead_events` | Every state change, score and requirement update |
| | `lead_matches` | Ranked matches with reasons `{en, hi}` |
| | `objections` | Type, text, language, handled |
| Visits | `site_visits` | `visit_date`, `slot`, `state`. CHECK: confirmed/completed/no_show require `confirmed_by` |
| | `site_visit_events` | Every visit transition with actor |
| Handoff | `handoffs` | Assigned salesperson (config email + optional user), context snapshot JSON |
| | `follow_ups` | Channel, language, schedule, status, provider message ID |
| Webhooks | `webhook_events` | (`provider`, `event_id`) unique: dedupe, idempotency, replay protection |
| Usage | `usage_records` | Kind, provider, quantity, unit, cost; indexed by (`kind`, time) |
| | `agent_state` | Single row; the kill switch |

## Commercial columns

Every Truth table carries `status` (`draft`/`approved`/`archived`), `approved_by`,
`approved_at`, `valid_from`, `valid_to`, `version`, `source_batch_id`, plus these constraints:

- `approved` rows must have `approved_by` and `approved_at` (`ck_<table>_approved_has_approver`).
- `valid_to >= valid_from` when both are set.
- `version >= 1`; a logical key + version is unique (e.g. `projects(code, version)`), and
  projects have a partial unique index so only one approved version per code exists.

There is **no stored "stale" status**. Staleness is computed by the Truth Layer (Stage 2): stale
when `approved_at` is older than `truth.stale_after_days` or `valid_to` has passed. Re-approval
clears it automatically.

## Versioning and logical keys (Stage 2, ADR 0008)

Each Truth row has a logical key that stays the same across versions:

| Table | Logical key |
| --- | --- |
| `projects` | project code |
| `unit_types` | project code + unit type code |
| `units` | project code + unit number |
| `price_lists` | project code + unit type code + unit number (one of the last two, or neither for a project-level list) |
| `offers` | project code + title |
| `payment_plans` | project code + plan name |
| `amenities` | project code + name |

An upload with no errors writes each row as a new `draft` version (`version` = previous max + 1,
`source_batch_id` = the batch). Approval turns the batch's drafts `approved` and archives the
approved versions they replace; rejection archives the drafts. Nothing is deleted. Foreign keys
point at the parent version current at import time, so queries always resolve parents through
their code.

## Migrations

| Revision | Content |
| --- | --- |
| `0001` | All Stage 1 tables, `vector` extension |
| `0002` | `alerts` (partial unique index: one open alert per kind); `validation_errors.severity`, `.entity`; `import_batches.filename`, `.summary`, `.archived_at`; `archived` batch status |
| `0003` | `customer_identities`: unique (`identity_type`, `value`) replaced by a partial unique index `WHERE verified`; `messages.meta` |

## Indexes from the plan

- Phone/email lookups: `customer_identities(identity_type, value)` unique where verified, plus `value`;
  `users(lower(email))` unique.
- `units(project_id, status, bhk, price_inr)` for structured search.
- `leads(state, score)`.
- `site_visits(visit_date)` and `site_visits(project_id, visit_date, slot)` for slot capacity.

## Embeddings

`document_chunks.embedding` is `vector(768)`. The dimension is fixed for the template;
`embeddings.dimensions` in config must be 768 (validated at startup). Changing it is a template
change with a new migration, not a per-client setting.

## Retention

Transcripts and recordings are purged or anonymised by a Stage 8 job using
`retention.transcript_days` and `retention.recording_days`.
