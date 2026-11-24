# ESTARA Pro

Single-tenant AI sales employee for Indian real-estate developers: answers enquiries in English,
Hindi and Hinglish, recommends properties from approved data only, pushes for a site visit, and
hands the lead to a human salesperson with full context.

- Product spec: [`ESTARA_MASTER_SPEC.md`](ESTARA_MASTER_SPEC.md)
- Standing instructions for the build: [`CLAUDE.md`](CLAUDE.md)
- Build plan and stage prompts: [`docs/roadmap/phase-plan.md`](docs/roadmap/phase-plan.md),
  [`ESTARA_STAGE_PROMPTS.md`](ESTARA_STAGE_PROMPTS.md)
- What is done, tested or blocked: [`docs/progress.md`](docs/progress.md)

## Quick start

```bash
cp .env.example .env    # fill in passwords and SESSION_SECRET
make install
make up && make migrate && make seed-sample
make check
```

Dashboard: http://localhost:3000 · API health: http://localhost:8000/api/v1/health.
Details and Windows commands: [`docs/deployment/deployment-guide.md`](docs/deployment/deployment-guide.md).

## Per-client changes

Only `client/` (config, `scripts/en`, `scripts/hi`), `.env` and uploaded data change between
clients.

- Set up a new client: [`docs/new-client-setup.md`](docs/new-client-setup.md)
- Put it live on the client's server (HTTPS, Vapi, WhatsApp, website buttons, backups):
  [`docs/deploy.md`](docs/deploy.md)
- Demo on your own PC at zero cost: [`docs/demo.md`](docs/demo.md)
- Before real customers: [`docs/before-first-customer.md`](docs/before-first-customer.md)
- Security: [`docs/security/security-review.md`](docs/security/security-review.md)
