# Security design

How ESTARA is protected. What was checked for the release, and the known limits, are in
[security-review.md](security-review.md).

## Secrets

- Secrets live only in `.env`, never in git (`.gitignore` excludes `.env`). `.env.example` lists
  every variable. **implemented**
- `SESSION_SECRET` must be ≥ 32 characters or the app refuses to start. **implemented**
- Secret scanning (gitleaks) and dependency audits in CI. **implemented**

## Dashboard authentication

- Passwords hashed with argon2id (`argon2-cffi`); minimum 12 characters; transparent rehash on
  login when parameters change. Unknown emails take the same code path to avoid timing leaks.
  **implemented**
- Server-side sessions. The browser holds a random 256-bit token in the `estara_session`
  cookie (`HttpOnly`, `Secure`, `SameSite=Lax`). The database stores only an HMAC-SHA256 digest
  keyed by `SESSION_SECRET`. Sessions expire after `SESSION_TTL_HOURS`; logout revokes them.
  **implemented**
- CSRF: double-submit token. Login returns a CSRF token (also in the readable `estara_csrf`
  cookie); every POST/PUT/PATCH/DELETE must send it in `X-CSRF-Token`, checked against the
  session's stored digest. A middleware also rejects state-changing `/api` requests whose
  `Origin` is not `DASHBOARD_ORIGIN`. **implemented**
- Login protection: per-IP fixed-window rate limit (Redis; `LOGIN_IP_MAX_ATTEMPTS` per
  `LOGIN_IP_WINDOW_SECONDS`, 429) and per-account lockout after `LOGIN_LOCKOUT_THRESHOLD`
  failures for `LOGIN_LOCKOUT_MINUTES` (423). Error messages never say whether the email exists.
  **implemented**

## Authorisation

- Roles: `client_admin` (everything) and `sales_staff` (leads, visits, handoffs; cannot approve
  data or change controls). Dependencies `CurrentUser`, `StaffUser`, `AdminUser`. **implemented**
- Route guard: two tests fail if any `/api` route outside the public list is reachable without a
  session — a static check of each route's dependencies and an HTTP check that calls every
  OpenAPI operation anonymously and expects 401. Public: `/api/v1/health*`,
  `/api/v1/auth/login`, `/api/v1/webhooks/*` (signature-verified). **implemented**
- Only authenticated staff can confirm site visits; enforced in code and by a database
  CHECK constraint. **tested**

## Uploads

- File type is taken from the content (PDF magic bytes, ZIP containing `xl/workbook.xml` or
  `word/document.xml`, otherwise UTF-8 text for CSV) and must match the extension. **tested**
- XLSX/DOCX: refused if they contain macros/ActiveX, more than 2,000 parts, expand beyond 100 MB,
  or have a compression ratio above 200 (zip bombs). Parsed with openpyxl in read-only,
  values-only mode (no formulas evaluated) with defusedxml installed. **tested**
- Limits: 10 MB data, 25 MB documents, 5,000 rows per sheet, 200 PDF pages; encrypted PDFs
  refused. Text cells starting with `=` or `@` are rejected (spreadsheet formula injection).
  **tested**
- Stored under the SHA-256 of the content, never the uploaded name. **implemented**
- Upload and review: any staff user. Approve, reject, archive: `client_admin` only, audited
  (`datacenter.upload`, `datacenter.batch_approved`, `…_rejected`, `…_archived`). **tested**
- Malware scanning by an antivirus engine is not included; files are never executed or opened
  by anything but the parsers above.

## Audit

`app/audit/service.record()` writes append-only entries. Logged now: login success/failure,
lockout blocks, logout, user creation, Data Center uploads, approvals, rejections and archiving.
Also visit confirmations and pause/resume. **implemented**

## Logging

A logging filter redacts phone numbers, email addresses, bearer tokens and `key=value` secrets
from every record. **implemented**

## Webhooks

Every provider webhook verifies its signature or secret, stores the provider event ID in
`webhook_events` (unique per provider), rejects replays and processes idempotently. **implemented**

## Transport and headers

TLS terminates at Caddy (`docker-compose.prod.yml`), which also adds HSTS and other security
headers. API docs are disabled when `ENVIRONMENT=production`. **implemented**

## India-specific requirements (documented, not claimed)

Call-recording consent, AI disclosure, TRAI/DND rules for outbound calls (outbound stays off),
WhatsApp opt-in and opt-out, and personal-data handling under the DPDP Act 2023 all apply. The
sample disclosure texts are placeholders. **The client or a lawyer must review them; ESTARA
does not claim compliance.**
