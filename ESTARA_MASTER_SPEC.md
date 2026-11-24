# ESTARA Pro — Master Specification

Version 1.2 (checked line by line against the brief on 2026-10-13).
Sources, in order of authority: `ESTARA_PRO_BUILD_PROMPT.md` (the brief), `CLAUDE.md`,
`docs/roadmap/phase-plan.md`, `ESTARA_STAGE_PROMPTS.md`. Where the brief and CLAUDE.md disagree,
the build stops and asks. Brief sections are cited as (§n). Section 8 lists every difference
found and how it was resolved.

## 1. Product

ESTARA Pro is an AI sales employee for real-estate developers in India, not a chatbot or a demo
(§1). In every conversation it understands the customer's need, recommends matching properties
from approved data, handles objections, and **persuades the customer to book a site visit**,
then hands the lead to a human salesperson with full context. Human salespeople close the deal.

- **Single-tenant clone template** (§1, ADR 0001). One master template repository; per client:
  clone, fill `client/client.config.yaml` and `.env`, upload the client's data, deploy.
- Each client has its own deployment, database, Vapi assistant and API keys, under the client's
  own accounts. Isolation is physical: no `tenant_id`, no row-level security.
- The code is identical for every client. Only `client/` (config, scripts, branding), `.env` and
  uploaded data change. If a client needs a code edit, the template is wrong: move the need into
  configuration or into the template.
- **Channels** (§1): telephone first (the client's sales number via call forwarding or SIP), then
  website chat, WhatsApp and email. All channels share one Sales Brain, one Truth Layer and one
  customer record.
- **Scope** (§1): Pro tier only. Not built: CRM integration, calendar sync, custom scripts beyond
  `client/scripts`, and multi-language beyond configuration hooks. Pro speaks English and Hindi
  (Hinglish as the norm, ADR 0004) through the `agent.languages` hook; a third language is
  Premium.
- The frozen Build 1 demo is separate; none of its internals are reused (§2).

## 2. Requirements

### 2.1 Functional

| ID | Requirement | Brief | Stage |
| --- | --- | --- | --- |
| F1 | Load and validate `client/client.config.yaml` at startup; refuse to start on any problem. The config covers company, agent name, tone, greeting, languages, branding, sales team, escalation, human transfer number, working hours, channels, tier, site visits, lead scoring, scripts and disclosures (as files), usage limits | §5 | 1 |
| F2 | Dashboard login with two roles, `client_admin` and `sales_staff`, with role-based permissions | §11, §12 | 1 |
| F3 | Data Center: upload → scan → parse → validate → preview → review → approve → import → make approved facts available. CSV, Excel, PDF, DOCX; downloadable templates; validation errors, duplicates, source tracking, version history, approval controls, archival, stale-data detection | §6 | 2 |
| F3a | Manual entry of individual rows in the dashboard, through the same validate/approve path | §6 ("where practical") | 7 |
| F4 | Truth Layer serves only approved, non-stale, non-contradicted prices, availability, offers, payment plans, possession dates, areas and **amenities**, pre-formatted for speech in en and hi | §7 | 2 (amenities: 3) |
| F5 | Brochures, FAQs and legal documents (PDF/DOCX) searchable once approved; never used for prices or availability | §4, §7 | 2 |
| F6 | Customer profiles, conversations, messages, preferences and follow-up context persisted; identity resolution across channels without wrongly merging different customers | §8 | 3 |
| F7 | Sales Brain shared by all channels: intent and requirements (budget, location, property type, BHK, timeline, loan need), follow-up questions, structured inventory search, explainable ranking, retrieval of approved documents, objection handling (price, location, "will think about it", competitor), steering to a site visit with concrete options, deterministic qualification, escalation on uncertainty | §7 | 3 |
| F8 | Structured property matching with explainable ranking (reason per match, en and hi) | §7 | 3 |
| F9 | Lead lifecycle with documented states and transitions (ADR 0007); scores and qualification from backend rules driven by config | §8 | 3 |
| F10 | Vapi tool backend built and tested before any live assistant changes; assistant generated per call by the backend; webhook signatures verified; call recording link, transcript, summary, duration, outcome and cost stored | §9 | 4 |
| F11 | Site visits (requested, confirmed, completed, rescheduled, cancelled, no-show): the AI may request; only an authorised person confirms | §8 | 5 |
| F12 | Human handoff delivering requirements, relevant conversation history, matched properties, qualification and recommended next action (plus objections raised); follow-up scheduling for leads who did not book | §8 | 5 |
| F13 | Website widget (one script tag), WhatsApp (Meta Cloud API or authorised provider) and email on the same Sales Brain and customer record | §10 | 6 |
| F14 | Client dashboard on real data only (section 5); usage and cost tracking, limits, alerts, kill switch | §11, §12 | 5, 7 |
| F15 | Security verification, observability, backups with a tested restore, `setup_client.sh`, clone guide, template versioning with tags and an update procedure, clone rehearsal, end-to-end evaluation | §5, §12, §13 | 8 |

### 2.2 Voice tools (§9)

The brief's backend tools map to `POST /api/v1/vapi/tools` (contract in the phase plan). The
Sales Brain uses the same tool functions for every channel.

| Brief capability | Tool |
| --- | --- |
| Property search | `search_properties` |
| Property details | `get_property_details` |
| Verified commercial info | `get_commercial_info` (fact type from the one enum, including `amenity`) |
| Knowledge retrieval | `search_knowledge` |
| Customer context | `get_customer_context` |
| Lead creation | Automatic: the backend creates the lead on the first message or call of a customer with no open lead; `save_requirements` also ensures one exists |
| Lead evaluation | `evaluate_lead` |
| Site-visit request | `get_visit_slots`, `request_site_visit` |
| Human transfer | `transfer_to_human` |
| Follow-up | `schedule_follow_up` |

Tools are built and tested before any live assistant is changed. If a Vapi assistant already
exists, its current tool contract at `POST /api/v1/vapi/tools` is inspected first; no
undocumented schema is assumed (§2, §9).

### 2.3 Non-functional

- **Architecture** (§4): modular monolith (FastAPI, Pydantic, PostgreSQL with SQLAlchemy and
  Alembic, Redis with ARQ only where needed, Next.js dashboard deployable on Vercel). No
  microservices, Kubernetes or Kafka. Backend on a container host or VPS, not serverless.
- **Adapters** (§4): every external service (LLM, voice, telephony, WhatsApp, email, embeddings)
  behind an adapter. The LLM provider is a config value (`llm.provider`: `gemini | claude |
  openai`, the brief's `llm_provider`); Gemini by default; swapping is a config change.
- **Latency:** each Vapi tool call p95 < 800 ms; knowledge retrieval < 400 ms, max 3 chunks.
- **Languages:** English and Hindi everywhere; Hinglish is the normal case.
- **Honesty:** no invented facts. Zero hallucinated prices or dates is a release gate.
- **Cost** (§4): free tiers and open-source tools during development, with fake or sample data
  only; paid tiers are required before real customers. No recurring paid service is added
  silently.
- **Security:** section 3.4 and `docs/security/security-design.md`.

## 3. Business rules

### 3.1 Truth Layer (never broken)

1. Prices, availability, offers, payment plans, possession dates, areas and amenities come only
   from approved, non-stale rows served by `app/truth/` (§7). Unapproved, missing or
   contradictory commercial information never becomes a confident claim (§6).
2. Fact types use one enum everywhere: `price`, `availability`, `offer`, `payment_plan`,
   `possession`, `area`, `amenity`.
3. Staleness is computed, never stored: a fact is stale when `approved_at` is older than
   `truth.stale_after_days`, or `valid_to` has passed. Re-approval clears it. Every fact type in
   `truth.block_on_stale` (all seven in the sample config) is refused when stale.
4. Missing, stale, unapproved or contradictory facts return `unavailable` with a reason code.
   The agent says it will confirm with the sales team; it never guesses.
5. The LLM interprets language and writes replies. It never produces a number or date itself,
   never decides commercial truth, never grants permissions and never bypasses backend
   authorisation (§7). Tools return spoken strings (lakh/crore, Hindi number words, Hindi month
   names).
6. pgvector holds document chunks only, never prices or availability (§4). Amenities are told
   from the Truth Layer, not from documents.

### 3.2 Leads and visits

- Lead states (§8): `new_enquiry`, `engaged`, `qualified`, `visit_requested`, `visit_confirmed`,
  `handed_off`, `nurturing`, `won`, `lost`. Every transition is logged in `lead_events`.
- Qualification, scores and transitions come from deterministic backend rules driven by
  `lead_scoring` config, never from the LLM (§7, §8). Bands: hot ≥ `thresholds.hot`,
  warm ≥ `thresholds.warm`, else cold.
- Allowed transitions, triggers and actors: ADR 0007. Summary:

  | From | To |
  | --- | --- |
  | new_enquiry | engaged, qualified, handed_off, lost |
  | engaged | qualified, nurturing, handed_off, lost |
  | qualified | visit_requested, nurturing, handed_off, won, lost |
  | visit_requested | visit_confirmed, nurturing, handed_off, won, lost |
  | visit_confirmed | visit_requested (reschedule), nurturing, handed_off, won, lost |
  | handed_off | visit_requested, visit_confirmed, nurturing, won, lost |
  | nurturing | engaged, qualified, visit_requested, handed_off, won, lost |
  | won, lost | (terminal; new contact creates a new lead) |

  Only staff can move a lead to `visit_confirmed`, `won` or `lost`. `handed_off` means a human
  owns the lead: after a transfer or escalation, or when the assigned salesperson accepts the
  handoff (the brief's "then hand the lead to a human salesperson", §1). A visit request creates
  a handoff record but the lead stays `visit_requested` until then.
- Visit states (§8): `requested`, `confirmed`, `completed`, `rescheduled`, `cancelled`,
  `no_show`. The AI may only request. Confirm, reschedule, complete and no-show require a
  `client_admin` or `sales_staff` user and are audited. The database enforces that confirmed,
  completed and no-show visits name the confirming user.
- Slots come from `site_visits` config; at most `slot_capacity` visits per project per slot.

### 3.3 Channels, disclosure and compliance

- Outbound calls are off (`compliance.outbound_calls_enabled: false`). Follow-ups go via
  WhatsApp; config rejects `voice` follow-ups while outbound is off.
- Recording disclosure: at the start of every call, from `scripts/{lang}/recording_disclosure.md`.
  AI disclosure: whenever the caller asks (§9); the template also says it in the opening lines,
  which is stricter than the brief requires. Both texts come from config files.
- WhatsApp: official Cloud API or authorised provider, webhook verification, deduplication,
  idempotency, retries, delivery status, opt-out (§10). Opt-out matches only when the whole
  trimmed, case-insensitive message equals a keyword from `channels.whatsapp.opt_out_keywords`.
- India: call-recording consent, AI disclosure, TRAI/DND rules for outbound calls, WhatsApp
  opt-in and personal-data handling are documented, not claimed. **The client or a lawyer must
  review them** (§12). Sample disclosure texts are placeholders.
- No integration is called live until a real test has verified it; until then adapters are
  tested against clearly labelled mocks and the dependency is documented (§9, §10). Indian
  number availability on a free Vapi plan is not assumed; provider and KYC requirements are
  documented (§9).

### 3.4 Security and operations (§12)

Authentication with secure sessions, role-based authorisation, input validation and rate
limits; webhook signature verification and replay protection; secrets only in `.env`, never in
code, logs, browsers or anything a customer sees; redacted logs; audit records for approvals,
visit confirmations, config changes and data exports; real provider usage and cost tracking with
configurable limits, timeouts, retries, kill switch, monitoring and alerts; backups with a
tested restore.

## 4. User journeys

### 4.1 English caller (voice)

1. Caller dials the developer's number; it forwards to Vapi. Vapi sends `assistant-request`.
2. Backend builds the assistant: bilingual greeting, disclosures, Sales Brain prompt, tools.
3. Caller: "I'm looking for a 2 BHK in New Town, around 80 lakh." The agent asks one question
   at a time (timeline, loan, purpose) and calls `save_requirements`.
4. Agent calls `search_properties`; reads the top matches using the Truth Layer's spoken
   strings ("72 lakh rupees", "possession December 2027").
5. Agent offers concrete visit slots from `get_visit_slots`; caller picks Saturday 11:00 and
   gives a name. `request_site_visit` creates a `requested` visit and a handoff.
6. Sales staff see the lead, call summary, matched property and visit request on the
   dashboard and confirm the visit. The customer gets a WhatsApp confirmation.

### 4.2 Hindi-speaking caller (voice)

1. Greeting plays in English then Hindi (`greeting_mode: bilingual`).
2. Caller: "मुझे दो BHK चाहिए, बजट अस्सी लाख तक है।" Language is detected as `hi`, stored on
   the conversation and as `customers.preferred_language`.
3. The agent replies in natural spoken Hinglish in Devanagari ("ठीक है, New Town में दो BHK
   देख लेती हूँ"). Project names and "BHK" stay in English.
4. Prices come from the Truth Layer in Hindi words ("बहत्तर लाख रुपये", "डेढ़ करोड़ रुपये");
   dates as "दिसंबर 2027".
5. A visit is requested exactly as in 4.1. The dashboard shows the transcript as spoken and an
   English summary for staff.
6. When the same number calls again, it is greeted in Hindi.

### 4.3 Hinglish caller with a language switch

1. Caller: "Hi, mujhe Rajarhat side pe 3 BHK chahiye, budget ek se dedh crore." The extractor
   reads the budget as ₹1,00,00,000 to ₹1,50,00,000.
2. The agent answers in Hinglish. Mid-call the caller switches to English ("Can you tell me
   about the payment plan?"); the agent follows, and the switch is logged on the message.
3. The payment plan is approved but stale. `get_commercial_info` returns
   `unavailable/stale`; the agent says the team will confirm it, and offers a visit.

### 4.4 Staff journeys

- **Client admin:** signs in, uploads inventory, reviews validation errors, approves data,
  watches usage, pauses the agent (kill switch).
- **Sales staff:** signs in, reviews handoffs and leads, confirms or reschedules visits. Cannot
  approve data or change controls.

## 5. Dashboard (§11)

Real backend data only: no hardcoded statistics, fabricated leads or static data; every screen
has loading, empty, error and success states. Roles: client admin and sales staff. No
multi-tenant operator panel; a small health/status page instead.

| Screen | Contents | Stage |
| --- | --- | --- |
| Home | New leads today, calls handled, visits requested, visits confirmed, response time | 7 |
| Leads list | Name, phone, source, status, score, last contact, next action | 5, 7 |
| Lead detail | Budget range, property type, BHK, location, timeline, loan need, objections, matched properties, qualification reason, full conversation history | 5, 7 |
| Calls | Recording link, transcript, summary, duration, outcome, cost | 5, 7 |
| Conversations | One unified thread per customer across channels, showing language | 7 |
| Site visits | Requested, confirmed, completed, rescheduled, cancelled; confirm and reschedule for authorised staff | 5, 7 |
| Handoffs | Assigned salesperson, context summary, status | 5, 7 |
| Properties and projects | Approved data, last updated, stale warnings | 2 (built) |
| Data Center | Upload, validation, approval, import status, freshness; manual entry (F3a) | 2 (built), 7 |
| Usage and cost | Call minutes, LLM usage, messages, estimated spend, limits | 7 |
| Controls | Pause/resume agent (kill switch), permitted settings | 7 |
| Status | Health of database, Redis, migration, voice readiness | 1 (built) |

## 6. Failure behaviour

| Situation | Behaviour |
| --- | --- |
| Invalid config or missing translation | Process exits at startup with a list of every problem |
| Voice fields (`voice.tts`, `voice.stt`) still `null` | No live assistant is built; calls transfer to `escalation.human_transfer_number` |
| Fact missing / stale / unapproved / contradictory | `unavailable` + reason code; agent promises confirmation |
| Tool error or timeout | Structured `unavailable`; never a stack trace read aloud |
| Caller asks for a human, AI or a provider fails, kill switch on, usage limit hit | Transfer to the human number (§7, §9) |
| Backend unreachable | Vapi's configured fallback destination forwards to the human number |
| After hours, no human | Take requirements, create the lead, promise a callback (`after_hours.md`) |
| Duplicate or replayed webhook | Ignored via `webhook_events` (provider, event_id) uniqueness |
| Uncertain identity match | Flag "possible duplicate" for staff; never auto-merge |
| Repeated failed logins | Account locked for `LOGIN_LOCKOUT_MINUTES`; per-IP rate limit returns 429 |

## 7. Acceptance criteria (§15)

ESTARA Pro is complete when the user can:

1. Clone the template for a new client, change only `client.config.yaml`, `.env` and uploaded
   data, and deploy with documented steps.
2. Have the client upload and approve its own property data.
3. Connect the client's phone number (forwarding or SIP) and verify a real test call.
4. Have the agent answer using only approved data (in English, Hindi and Hinglish), qualify the
   lead, and push for a site visit.
5. See the lead, call details, customer requirements, matched properties and visit request on
   the dashboard.
6. Have a human confirm the visit and receive a handoff with full context.
7. Have website, WhatsApp and email share the same customer context.
8. Pause the agent, see usage and cost, restore from backup, and fall back to a human number if
   AI fails.

Production readiness is not claimed until these pass end to end (§14). Stage-level "done when"
checks are in `ESTARA_STAGE_PROMPTS.md`; results are recorded in `docs/progress.md`.

## 8. Reconciliation with the brief (2026-10-13)

| Difference found | Resolution |
| --- | --- |
| §7 makes the Truth Layer authoritative for amenities; CLAUDE.md, the plan and the code had six fact types without them | `amenity` added to the fact-type enum and the Truth Layer (approved, non-stale, spoken en/hi, sources); added to the sample `truth.block_on_stale`. ADR 0009. CLAUDE.md and `docs/roadmap/phase-plan.md` still say "six" and are left for the user to update |
| §6 asks for manual entry "where practical"; not in the spec or built | Added as F3a, scheduled with the full dashboard (Stage 7), through the same validate/approve path |
| §9 lists "lead creation" as a backend tool; the plan's tool contract has none | Mapped in section 2.2: automatic on first contact, also ensured by `save_requirements` |
| §9 requires AI disclosure "when asked"; the spec said every call | Both: when asked (required) and in the opening lines (template default, stricter) |
| §1, §8: "then hand the lead to a human salesperson"; ADR 0007 only reached `handed_off` by transfer or escalation | ADR 0007: the assigned salesperson accepting the handoff also moves the lead to `handed_off` |
| §8 handoff contents and §11 screen contents were not spelled out | Listed in F12 and section 5 |
| §1 (client's own accounts and keys), §2 (Build 1 demo separate; inspect an existing Vapi assistant first), §4 (architecture limits, cost rule), §12 (no secrets in browsers), §14 (no production-readiness claim before acceptance) | Added to sections 1, 2.2, 2.3, 3.4 and 7 |
| §1 excludes "multi-language beyond configuration hooks" | Read as: en/hi through `agent.languages` is Pro; a third language is Premium. Consistent with CLAUDE.md |
| §4 names the config key `llm_provider`; the plan's config design uses `llm.provider` | Same setting; the plan's nesting is kept (section 2.3) |
| Journey example prices did not match the sample data, and Hindi amounts lacked "रुपये" (ADR 0008) | Examples updated to sample prices and the currency word |
| §8 lead states | Identical to ADR 0007's nine states; no change |
