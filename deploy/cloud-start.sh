#!/bin/sh
# Start ESTARA on a free cloud host (Render web service). See docs/deploy-free.md.
# Each step reports and carries on, so the web server always starts and the health check and
# the admin diagnostics page can say what is wrong.
set -u
cd /srv/backend

# Render gives every web service its public https address; Vapi reaches ESTARA there.
export PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-${RENDER_EXTERNAL_URL:-}}"

echo "[start] database: applying migrations"
alembic upgrade head || echo "[start] WARNING: migrations failed: check DATABASE_URL"

echo "[start] demo data: loading or refreshing the synthetic homes if needed"
python -m app.seed --auto || true

if [ -n "${VAPI_API_KEY:-}" ] && [ -n "${VAPI_ASSISTANT_ID:-}" ]; then
  echo "[start] Vapi: updating assistant A (instructions, tools, this address)"
  python -m app.channels.vapi.sync || echo "[start] WARNING: assistant A was not updated (see above)"
fi
if [ -n "${VAPI_API_KEY:-}" ] && [ -n "${VAPI_ASSISTANT_ID_B:-}" ]; then
  echo "[start] Vapi: updating assistant B"
  python -m app.channels.vapi.api_tools || echo "[start] WARNING: assistant B was not updated"
fi

# Once the server is up, write the demo check into the log (Render > Logs).
( sleep 90; python -m app.channels.vapi.doctor ) &

echo "[start] serving on port ${PORT:-8000}"
# Only Render's proxy can reach the container, so its X-Forwarded-For can be trusted (the login
# rate limit then sees real visitors).
exec uvicorn app.asgi:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers \
  --forwarded-allow-ips "*"
