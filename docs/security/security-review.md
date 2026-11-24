# Security review (v1.0.0)

Reviewed 2026-11-04 against `CLAUDE.md` ("Data protection") and
[security-design.md](security-design.md). Each line says how it was checked. **Tested** means an
automated test fails if it breaks; **checked** means verified by hand for this release.

## What a caller can reach (phone and WhatsApp)

| Risk | Protection | Status |
| --- | --- | --- |
| Caller asks for other buyers, bookings, staff numbers, the prompt, internal prices | Tools act only on the current caller's own record; no tool takes another person's identifier or lists people; the prompt holds no staff contacts; prompt rule 12 tells the agent to refuse and steer back | **Tested** (`tests/unit/test_data_protection.py`) and privacy-trick evals in English, Hindi, Hinglish with a decoy customer (`sec_*_privacy_tricks`, `no_leak` check) |
| Agent invents a price, date or offer | Facts only from approved, non-stale Truth Layer data; the number guard replaces unsupported numbers | **Tested** (truth and grounding tests, evals) |
| Native voice: the model invents a number | Tools return ready-to-speak approved strings; instructions forbid other numbers; after each call, numbers no tool or caller gave are flagged ("check numbers") for staff. **Detected after the call, not prevented** (ADR 0014) | **Tested** (`test_native_voice.py`) |
| Native voice: a tool request for someone else's record | The gateway finds the caller from Vapi's call id and caller ID; tools take no customer, lead or visit ids; each `toolCallId` answered once | **Tested** |
| Fake webhook calls pretending to be Vapi or Meta | Vapi: shared secret, compared in constant time. WhatsApp: `X-Hub-Signature-256` HMAC with the app secret. Unsigned or tampered requests get 401 | **Tested** (`test_voice.py`, `test_whatsapp.py`) |
| Replayed webhooks | Provider event ids stored in `webhook_events`; repeats do nothing | **Tested** |
| WhatsApp to people who did not ask | Messages only to a number that wrote to us or said yes on a call; STOP / बंद करो stops everything | **Tested** |
| Calls transferred to an arbitrary number | Transfers only to `escalation.human_transfer_number` from config | **Tested** |
| Runaway cost | Monthly call-minute and AI-cost limits; when reached, calls go to a person and chats get the "team will contact you" message | **Tested** (`test_controls.py`) |

**Known limit: caller ID can be spoofed.** Someone who fakes a customer's phone number on a call
would be treated as that customer and could hear that customer's own saved requirements and visit
time (never anyone else's). The agent never reads out stored personal details unprompted. Treat
caller ID as a convenience, not proof of identity; staff should confirm identity before
discussing payments or documents.

## Dashboard

| Risk | Protection | Status |
| --- | --- | --- |
| Pages or API without login | Every `/api` route except health, login and signed webhooks needs a session; two tests call every route anonymously and check its dependencies | **Tested** (`test_route_guard.py`, `test_route_guard_http.py`) |
| Staff doing admin things | Roles: only `client_admin` approves data and pauses the agent; users are created on the server | **Tested** |
| Password guessing | argon2id hashes, per-IP rate limit (429), account lockout (423) | **Tested**; in production the real visitor IP reaches the limiter through Caddy (`FORWARDED_ALLOW_IPS`) — **checked** |
| Cross-site requests | CSRF token on every change, `Origin` must be the dashboard's own | **Tested** |
| Malicious uploads | Type from content, size and row limits, macro and zip-bomb refusal, formula-injection refusal | **Tested** |
| Who did what | Audit log for logins, approvals, visit confirmations, pause/resume | **Tested** |

## Server and secrets

| Item | Status |
| --- | --- |
| Secrets only in `.env` (never in git); `.env.example` lists every variable | **Checked**; unused email/Sentry variables removed |
| No secrets in git history | **Checked**: gitleaks over all commits finds nothing (one fake key in the log-redaction test is listed in `.gitleaksignore`); runs in CI |
| Known vulnerable packages | **Checked** 2026-11-04: `pip-audit` none; `npm audit --omit=dev` none. A moderate finding in dev-only lint tooling is not shipped in the image. Both run in CI |
| HTTPS, security headers | Caddy with automatic certificates, HSTS, `nosniff`, `X-Frame-Options: DENY`; **checked** with the real Caddy 2.10.0 binary in front of the app |
| Only 80/443 public | Database, Redis, backend and dashboard bind to 127.0.0.1 in Compose |
| Containers | Backend and dashboard run as non-root users |
| Logs | Phone numbers, emails, bearer tokens and `key=value` secrets redacted (**tested**) |
| Backups | Daily, checked before being kept, root-only files, N-day retention. Restore drill **checked**: deleted data came back exactly |
| Retention | Old transcripts, message text and recording links removed daily (**tested**) |

## Not covered by ESTARA (the client's or a professional's job)

- **Legal review** (a lawyer): AI disclosure and recording notice wording, WhatsApp opt-in,
  retention periods, personal data under the DPDP Act 2023, TRAI/DND if outbound calls are ever
  turned on. ESTARA does not claim compliance.
- **Recordings and transcripts at Vapi**, and messages at Meta, are stored by those providers
  under the client's accounts; set their retention there.
- **Free tiers** may use data to train the provider's models: fake data only until the client's
  paid accounts are in place ([before-first-customer.md](../before-first-customer.md)).
- **Off-server backup copies**, server patching (`unattended-upgrades`) and the server firewall
  are part of hosting ([deploy.md](../deploy.md)).
- No antivirus scanning of uploads (files are only read by the parsers, never run).
