# Local development guide

Running ESTARA on your own PC for development and tests. To put a client live on their server
(HTTPS, backups), see [../deploy.md](../deploy.md).

## Prerequisites

- Docker with Compose v2 (Docker Desktop on Windows/macOS)
- Node 22 for the dashboard checks and Playwright
- Linux/macOS: GNU Make and Python 3.12 for host-side `make check`. Windows: see
  "Windows (no `make`)" below; Python on the host is not needed

## Local stack

```bash
cp .env.example .env
# Fill in at least: POSTGRES_PASSWORD (and the same value in DATABASE_URL / TEST_DATABASE_URL),
# SESSION_SECRET (python -c "import secrets; print(secrets.token_urlsafe(48))"),
# SEED_ADMIN_PASSWORD, SEED_STAFF_PASSWORD (12+ characters each).
# Browsers generally treat http://localhost as secure, so COOKIE_SECURE=true usually works
# locally; if sign-in does not stick, set COOKIE_SECURE=false for local use only.

make install       # .venv with backend deps, dashboard node_modules
make up            # postgres+pgvector, redis, backend, worker, dashboard (waits for health)
make migrate       # alembic upgrade head
make seed-sample   # sample admin and sales users, plus the sample inventory
make check         # lint, typecheck, tests
make test-e2e      # Playwright smoke tests against the running stack
make chat CHAT_LANG=hi   # talk to the Sales Brain (needs the LLM key; CHAT_LLM=offline without)
make eval          # conversation evals on the live LLM; WIPES TEST_DATABASE_URL
make eval EVAL_ARGS="--style hi"   # one language; or --id <id>; --llm-delay 15 to go slower
```

Services (bound to 127.0.0.1 only):

| Service | URL |
| --- | --- |
| Dashboard | http://localhost:3000 (`/login`, `/health`) |
| Backend API | http://localhost:8000/api/v1/health, docs at `/api/docs` outside production |
| Postgres | localhost:5432 (`estara`, plus `estara_test` for tests) |
| Redis | localhost:6379 |

Uploaded files live in the `uploads` volume (`UPLOAD_DIR=/srv/data/uploads`). The fake sample
inventory is mounted read-only at `/srv/sample_inventory` for `make seed-sample`, which imports it
through the normal upload → validate → approve path (running it again refreshes the data's
freshness). The dashboard's `/api` proxy accepts request bodies up to 30 MB
(`experimental.proxyClientMaxBodySize` in `dashboard/next.config.ts`).

`client/` is mounted read-only into the backend and worker at `/srv/client`. Editing a script
or the config needs a restart (`docker compose restart backend worker`); an invalid change stops
the service with a list of problems in `docker compose logs backend`.

## Windows (no `make`)

Every `make` target is a thin wrapper around `docker compose` or `npx`. In PowerShell, from the
repository root (Docker Desktop with the WSL 2 backend):

| make | PowerShell equivalent |
| --- | --- |
| *(setup)* | `Copy-Item .env.example .env`, then edit `.env` |
| `make up` | `docker compose up -d --build --wait` |
| `make migrate` | `docker compose run --rm --no-deps backend alembic upgrade head` |
| `make seed-sample` | `docker compose run --rm --no-deps backend python -m app.seed` |
| `make config-check` | `docker compose run --rm --no-deps backend python -m app.core.config` |
| `make check` (backend) | `docker compose --profile tools run --rm --build checks` |
| `make check` (dashboard) | `cd dashboard; npm ci; npx eslint .; npx tsc --noEmit; cd ..` |
| `make test-e2e` | `cd dashboard; npx playwright install chromium; $env:E2E_ADMIN_EMAIL="admin@example.com"; $env:E2E_ADMIN_PASSWORD="<SEED_ADMIN_PASSWORD from .env>"; npx playwright test; cd ..` |
| `make chat CHAT_LANG=hi` | `docker compose run --rm backend python -m app.brain.cli --lang hi` (add `--llm offline` without a key, `--debug` for details) |
| `make eval` | `docker compose --profile tools run --rm --build checks python -m tests.evals.run` (wipes the `estara_test` database; results land in `backend/tests/evals/results/`). One language: add `--style en` (or `hi`, `hinglish`); one conversation: `--id en_01_new_town_visit`; slower pacing: `--llm-delay 15`; longer response timeout: `--llm-timeout 180` |
| Gemini latency probe (no `make` target) | `docker compose --profile tools run --rm checks python -m app.brain.probe` (4 timed calls, 3 of them generation requests against your quota; `--thinking medium` / `--temperature 0.3` / `--model gemini-3.6-flash` to compare) |
| `make logs` | `docker compose logs -f` |
| `make down` | `docker compose down` |

Create an extra user (prompts for the password, so it never lands in shell history):

```powershell
docker compose run --rm --no-deps backend python -m app.auth.cli create-user `
  --email someone@example.com --name "Someone" --role sales_staff
```

Notes:

- `make eval` exits with 3 when the run is incomplete (rate limited, timed out or stopped) and
  1 when a conversation failed. Docker Desktop then prints "Debug this Compose error with
  Gordon"; that is Docker reacting to the non-zero exit code, not a Compose problem.
- Run the backend checks in the `checks` container, not in host Python on Windows. psycopg's
  async mode needs a selector event loop, which Windows does not use by default, so host-side
  `pytest` and `alembic` on Windows are not supported.
- `make up` used `--wait`; with older Compose versions without `--wait`, run
  `docker compose up -d --build` and check `docker compose ps` until all services are healthy.
- The `checks` service mounts `./backend` and uses the `estara_test` database, which the db
  container creates the first time its volume is initialised. If the volume predates that, run
  `docker compose exec db createdb -U estara estara_test` once.

## Running without Docker

If Docker images cannot be pulled, the same services run natively: PostgreSQL 16 with pgvector,
`redis-server`, then from `backend/`:

```bash
alembic upgrade head
uvicorn app.asgi:app --port 8000
arq app.jobs.worker.WorkerSettings
```

and in `dashboard/`: `BACKEND_URL=http://localhost:8000 npm run build && npm start`.

## Production

See [../deploy.md](../deploy.md): `docker-compose.prod.yml` adds HTTPS (Caddy), production
settings and daily backups.
