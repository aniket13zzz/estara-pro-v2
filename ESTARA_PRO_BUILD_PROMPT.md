# ESTARA PRO — MASTER BUILD PROMPT

You are my principal software architect, senior full-stack developer, AI engineer, database engineer, security engineer, and DevOps engineer.

Build **ESTARA Pro**: a production-ready AI Sales Employee for real-estate developers. Work phase by phase. Do not stop after writing documents. Do not fabricate test results, credentials, or provider capabilities.

---

## 1. PRODUCT DEFINITION

ESTARA Pro is an AI sales employee, not a chatbot or demo.

**It is NOT multi-tenant SaaS.** It is a **single-tenant clone template**:

- One master template repository.
- For each new client: clone the repo, fill `client.config.yaml` and `.env`, upload the client's data, deploy.
- Each client gets its own deployment, its own database, its own Vapi assistant, and its own API keys, under the client's own accounts.
- **The code is identical for every client. Only data and configuration change.** If a client needs a code edit, the template is wrong. Move the need into configuration or into the template itself.
- No `tenant_id` columns and no Row-Level Security. Isolation is physical (one client = one deployment).

**Primary goal of the agent in every conversation:** understand the customer's need, recommend matching properties from approved data, handle objections, and **persuade the customer to book a site visit**. Then hand the lead to a human salesperson with full context. Human salespeople close the deal.

**Primary channel: telephone** (the client's sales number, connected through call forwarding or SIP). Also website chat, WhatsApp, and email. All channels share one Sales Brain, one Truth Layer, and one customer record.

Only the **Pro** tier is in scope. Do not build Premium features (CRM integration, calendar sync, custom scripts, multi-language beyond configuration hooks).

---

## 2. START BY INSPECTING

1. Inspect the repository first. Preserve anything I explicitly ask to keep.
2. Do not treat existing files as an authoritative specification just because they exist.
3. The frozen Build 1 demo is separate. Do not reuse its internals.
4. If the repo is empty or unsuitable, create a clean project in the designated repo. Do not create unnecessary extra repositories.
5. If a Vapi assistant already exists, inspect its current tool contract at `POST /api/v1/vapi/tools`. Do not assume an undocumented schema.

---

## 3. CREATE THE DOCUMENTATION FIRST (then use it as source of truth)

Create these before major implementation, then keep them synchronized with the code:

- `ESTARA_MASTER_SPEC.md` — requirements, business rules, user journeys, acceptance criteria, failure behavior.
- `docs/architecture/architecture.md` — stack, components, security boundaries, integration architecture.
- `docs/architecture/database-design.md` — entities, relationships, indexes, migrations, retention.
- `docs/security/security-design.md` — auth, authorization, secrets, webhook security, personal-data protection.
- `docs/integrations/integration-design.md` — LLM, Vapi, telephony, WhatsApp, email, website contracts.
- `docs/testing/testing-strategy.md`
- `docs/deployment/deployment-guide.md` — local dev, environment, deploy, backups, monitoring, recovery.
- `docs/clone-guide.md` — exact steps to onboard a new client from the template.
- `docs/roadmap/phase-plan.md`
- `docs/decisions/` — ADRs for significant choices.

Record significant decisions rather than silently changing direction.

---

## 4. ARCHITECTURE (keep it simple and cheap)

Use a **modular monolith**. No microservices, Kubernetes, or Kafka.

| Layer | Choice |
|---|---|
| Backend | Python, FastAPI, Pydantic |
| Database | PostgreSQL, SQLAlchemy, Alembic migrations |
| Jobs/cache | Redis + a lightweight background job system, only where needed |
| Vector search | pgvector only for semantic retrieval of documents. **Never** as the source of structured prices or availability |
| Frontend | Next.js dashboard (deployable on Vercel) |
| Backend hosting | Container host or VPS (Render, Railway, Fly.io, or VPS). Not Vercel serverless, because of long AI calls and background jobs |
| Local dev | Docker Compose |
| Quality | Automated tests, linting, type checking, CI |

**Provider adapters** for every external service (LLM, voice, WhatsApp, email, telephony) so providers can be swapped without touching business logic.

**LLM adapter:** `llm_provider` in config = `gemini | claude | openai`. Default to Gemini for development. Swapping providers must be a config change, not a rewrite.

**Development cost rule:** use free tiers and open-source tools during development (Vapi free plan, Gemini free key, free Postgres, local Docker). Free tiers are for **fake or sample data only**. Document that paid tiers are required before real customers use the system. Do not silently add recurring paid services.

---

## 5. CONFIGURATION SYSTEM (core of the clone model)

Design `client.config.yaml` thoroughly in Phase 1 so no code edit is ever needed per client. It must cover:

- Company name, agent name, tone, greeting, languages, branding (logo, colors).
- Sales team contacts, escalation rules, human transfer phone number, working hours.
- Channels on/off, tier flag (`tier: pro`).
- Site-visit settings (days, time slots, lead-time rules, who confirms).
- Lead scoring thresholds and qualification rules.
- Objection-handling text and sales script text kept as editable files, not code.
- Call-recording disclosure and AI-disclosure text.
- Usage limits and cost alert thresholds.

Secrets (Vapi, LLM, WhatsApp, email keys) live in `.env` only. Never commit secrets. Provide `.env.example`.

Provide `./setup_client.sh` (or equivalent) that creates the database, runs migrations, seeds config, creates the admin user, and prints the Vapi webhook URL and next steps.

Version the template with tags and provide an update procedure for pulling template updates into client repos without conflicts.

---

## 6. DATA CENTER (client updates data without a developer)

Workflow: **Upload → Scan → Parse → Validate → Preview → Review → Approve → Import → Make approved facts available.**

- Support CSV, Excel, PDF, DOCX, and manual entry where practical.
- Clear validation errors, duplicate handling, source tracking, version history, approval controls, archival, stale-data detection.
- Never allow unapproved, missing, or contradictory commercial information to become a confident AI claim.
- Provide downloadable CSV/Excel templates so clients know the expected format.

---

## 7. TRUTH LAYER AND SALES BRAIN

**Truth Layer:** authoritative source for approved prices, availability, offers, payment plans, possession dates, amenities. If a fact is missing, stale, or contradictory, the agent says it will confirm with the sales team. It never guesses.

**Sales Brain:** shared across all channels. It must:

- Understand intent and requirements (budget, location, property type, BHK, timeline, loan need).
- Ask useful follow-up questions.
- Search inventory using structured criteria.
- Rank matches with explainable rules.
- Retrieve relevant approved documents.
- Handle routine objections (price, location, "will think about it", competitor).
- **Steer toward a site visit** with concrete options.
- Evaluate lead qualification with **deterministic backend rules**.
- Recognize uncertainty and escalate to a human.

The LLM may interpret language and generate responses. It must **not** decide commercial truth, grant permissions, or bypass backend authorization. Implement safe fallbacks when AI or providers fail (including transfer to a human number).

---

## 8. CUSTOMER MEMORY AND LEAD LIFECYCLE

- Persist customer profiles, conversations, messages, preferences, and follow-up context.
- Secure identity resolution across channels without wrongly merging different customers.
- Validated lead lifecycle with documented states and transitions: new enquiry, engaged, qualified, visit requested, visit confirmed, handed off, nurturing, won, lost.
- Scores and qualification come from backend logic, never blindly from the LLM.
- Site visits: requested, confirmed, completed, rescheduled, cancelled, no-show. **The AI may request a visit. Only an authorized person confirms it.**
- Human handoff delivers: requirements, relevant conversation history, matched properties, qualification, and recommended next action.
- Follow-up scheduling for leads who did not book.

---

## 9. VOICE (VAPI) — MAIN CHANNEL

- Vapi handles the call. The backend holds all intelligence. Vapi calls backend tools.
- Build and test backend tools **before** changing the live assistant: property search, property details, verified commercial info, knowledge retrieval, customer context, lead evaluation, lead creation, site-visit request, human transfer, follow-up.
- Verify Vapi webhook signatures. Keep tool responses fast and structured.
- Store call recording link, transcript, summary, duration, outcome, and cost.
- Agent discloses that it is an AI assistant when asked and that calls may be recorded (text from config).
- Fallback: if the agent fails or the caller asks for a person, transfer to the configured human number.
- Phone connection options: call forwarding (start here), SIP trunk, number porting. Document each.
- **Never claim a real number is live until a real test call has been verified.** If credentials are unavailable, build the boundary, test with clearly labelled mocks, and document the steps to enable real calls.
- Do not assume Indian number availability on a free Vapi plan. Document the provider and KYC requirements.

---

## 10. WEBSITE, WHATSAPP, EMAIL

- **Website:** embeddable chat widget via one script tag, using the same Sales Brain.
- **WhatsApp:** official Meta Cloud API or authorized provider. Webhook verification, deduplication, idempotency, retries, delivery status, opt-out handling.
- **Email:** secure credentials (OAuth or app password), inbound/outbound processing, conversation linking, provider-confirmed status, audit records.
- If credentials or approvals are missing, provide testable adapters and document the dependency. Do not pretend the integration is live.

---

## 11. CLIENT DASHBOARD (real data only)

Every screen uses real backend APIs and database records. No hardcoded statistics, fabricated leads, or static data. Include loading, empty, error, and success states.

- **Home:** new leads today, calls handled, visits requested, visits confirmed, response time.
- **Leads list:** name, phone, source, status, score, last contact, next action.
- **Lead detail:** budget range, property type, BHK, location, timeline, loan need, objections, matched properties, qualification reason, full conversation history.
- **Calls:** recording link, transcript, summary, duration, outcome, cost.
- **Conversations:** one unified thread per customer across channels.
- **Site visits:** requested, confirmed, completed, rescheduled, cancelled. Confirm and reschedule controls for authorized staff.
- **Handoffs:** assigned salesperson, context summary, status.
- **Properties and projects:** approved data, last updated, stale warnings.
- **Data Center:** upload, validation, approval, import status, freshness.
- **Usage and cost:** call minutes, LLM usage, messages, estimated spend, limits.
- **Controls:** pause/resume agent (kill switch), permitted settings.

Roles: client admin and sales staff, with role-based permissions. Skip the multi-tenant operator admin panel; provide a small health/status page instead.

---

## 12. SECURITY AND OPERATIONS

- Authentication, role-based authorization, secure sessions, input validation, rate limits.
- Webhook signature verification and replay protection.
- Secret management via environment, never in code or logs. Redacted logs.
- Audit records for sensitive actions (approvals, visit confirmations, config changes, data exports).
- Never expose provider secrets to browsers or customers.
- Track real provider usage and cost. Configurable limits, timeouts, retries, kill switch, monitoring, alerts.
- Backup and restore procedures, with a tested recovery.
- Document privacy and telecom requirements for India (call recording consent, TRAI/DND rules for outbound calls, WhatsApp opt-in). **Do not claim legal compliance without evidence.** State that the client or a lawyer must review.

---

## 13. PHASES (complete and report one gate at a time)

For each phase define entry conditions, tasks, tests, deliverables, exit criteria, and remaining external dependencies.

1. Product definition, documentation, architecture, repo foundation, **config system**.
2. Database schema and migrations (single-tenant).
3. Authentication and roles.
4. Data Center.
5. Truth Layer.
6. Customer identity, conversations, memory.
7. Sales Brain (Gemini first, adapter-based).
8. Property matching, lead qualification, lead management.
9. Vapi tool backend.
10. Voice integration and verification.
11. Site visits, human handoff, follow-up.
12. Website widget.
13. Client Dashboard.
14. WhatsApp integration.
15. Email integration.
16. Cost, usage, limits, kill switch.
17. Security verification.
18. Observability, backup, recovery.
19. Deploy tooling (`setup_client.sh`, clone guide, update procedure) and clone rehearsal on a fresh environment.
20. End-to-end evaluation.
21. Controlled pilot with one real client.
22. Production release.

**Demo milestone:** after Phase 11, I must be able to upload sample property data, call the agent, and see the lead, call summary, matched property, and visit request on the dashboard. Prioritize reaching this.

Refine phase boundaries only if a real dependency requires it, and document the reason first.

---

## 14. EXECUTION RULES

1. Inspect before modifying.
2. Write the documentation first, then implement.
3. Write automated tests alongside the implementation.
4. Run tests and fix failures you caused.
5. Record exact test commands and results.
6. Distinguish **implemented**, **tested**, and **externally blocked** features.
7. Never fabricate test results, provider responses, or successful deployments.
8. Do not mark a phase complete without evidence.
9. If a dependency is unavailable, continue independent work and document the exact blocker.
10. Keep development cost near zero without compromising security or correctness.
11. Do not add features or change the goal silently.
12. Do not claim production readiness until end-to-end acceptance criteria pass.
13. After each phase, report: what was built, tests run and results, open blockers, and what I must provide next.

---

## 15. FINAL ACCEPTANCE CRITERIA

ESTARA Pro is complete when I can:

1. Clone the template for a new client, change only `client.config.yaml`, `.env`, and uploaded data, and deploy with documented steps.
2. Have the client upload and approve its own property data.
3. Connect the client's phone number (forwarding or SIP) and verify a real test call.
4. Have the agent answer using only approved data, qualify the lead, and push for a site visit.
5. See the lead, call details, customer requirements, matched properties, and visit request on the dashboard.
6. Have a human confirm the visit and receive a handoff with full context.
7. Have website, WhatsApp, and email share the same customer context.
8. Pause the agent, see usage and cost, restore from backup, and fall back to a human number if AI fails.

The final deliverable is a real, implemented, tested, documented, cloneable product. Not a demo, not a plan, not mock interfaces.

---

**START NOW:** Inspect the repository. Create the Phase 1 documents and the `client.config.yaml` design. Set up the repo foundation (FastAPI, Postgres, Docker Compose, CI). Report the Phase 1 gate before moving to Phase 2.
