# 0002 — Modular monolith and stack

- Status: accepted
- Date: 2026-10-11

## Context

The product spans data import, a fact service, an LLM sales brain, voice, chat channels, lead
management and a dashboard. One small team builds and operates it, per client (ADR 0001).

## Decision

One repository with a modular monolith:

- **Backend:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic.
  Modules map to the spec (`core`, `db`, `auth`, `datacenter`, `truth`, `customers`,
  `conversations`, `brain`, `matching`, `leads`, `visits`, `handoff`, `channels`, `providers`,
  `usage`, `audit`, `jobs`).
- **Database:** PostgreSQL 16 with pgvector (documents only). Driver: psycopg 3 for both the app
  (async) and Alembic, so there is one driver to configure.
- **Jobs:** Redis + ARQ; the worker runs the same code base.
- **Dashboard:** Next.js App Router, TypeScript strict, same-origin `/api` via rewrites.
- **Widget:** plain TypeScript bundle (Stage 6).
- **Quality:** ruff, mypy strict on `app/`, pytest + pytest-asyncio, ESLint, tsc, Playwright.
- **Local:** Docker Compose. **CI:** GitHub Actions.
- External services only through adapters in `app/providers/`.

## Consequences

- One deployable backend image (API and worker) per client; simple to operate and clone.
- Module boundaries are enforced by convention and review, not process isolation; the Truth
  Layer boundary is additionally covered by tests from Stage 2.
- Versions used in Stage 1: FastAPI 0.143, SQLAlchemy 2.0, Next.js 16.4, React 19.3,
  ESLint 10, TypeScript 5.9. FastAPI 0.143 includes routers lazily, so the route-auth guard
  test checks routes both statically and over HTTP to stay robust across versions.
