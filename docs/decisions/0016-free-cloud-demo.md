# ADR 0016: Free cloud hosting for the public demo

Date: 2026-11-23. Status: accepted.

## Context

The owner needs a permanent public demo at ₹0, with the laptop off, deployed from GitHub. Client
deployments stay as they are (their own server, Docker Compose, Redis and the ARQ worker).

## Decision

- Website: Cloudflare Pages (free, commercial use allowed). Vercel's free Hobby plan is for
  non-commercial use only, so the demo moves off it.
- Backend: one Render free web service from `render.yaml` and `deploy/Dockerfile.cloud` (repo-root
  context, so `client/` and the demo homes are inside the image). `deploy/cloud-start.sh`
  migrates, runs `seed --auto`, updates the Vapi assistant with Render's address, then serves.
- Database: Neon free (Postgres with pgvector, no card, not paused for inactivity). Supabase free
  pauses projects with low activity over 7 days.
- Dashboard: Netlify free (Next.js runtime, commercial use allowed). It builds only when
  `dashboard/` changes, to save credits. Its `/api` proxy keeps the session cookie first-party.
- Redis and the worker are left out of the demo (`REDIS_ENABLED=false`, `RUN_JOBS_IN_WEB=true`).
  Redis was used only for the login rate limit, which falls back to memory. The worker's daily jobs
  run in the web process. WhatsApp notices are not part of the public demo.
- `DEMO_MODE=true` allows the synthetic homes with `ENVIRONMENT=production` and caps calls at
  5 minutes. A FAIL in the demo check is visible at `/api/v1/diagnostics` (admin only), because
  free hosts have no shell.

## Consequences

Free plans give no uptime guarantee. Render sleeps after 15 minutes without traffic, about a
minute to wake: an UptimeRobot ping and a wake-up request from the website reduce this, and it
fits Render's 750 hours only for one always-on service. Render's 0.1 CPU makes tool calls slower
than on a PC.
