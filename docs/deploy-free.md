# Free public demo: ESTARA in the cloud at ₹0 (Windows guide)

The public demo runs on free cloud services. Your laptop can be off. Every `git push` updates it.
It uses only synthetic demo data and its own database, never a client's.

```
Visitor's browser ──► Cloudflare Pages: the demo website (estara-demo.pages.dev)
       │  "Talk to Priya" (microphone)
       ▼
     Vapi (voice + AI) ──tools──► Render: ESTARA backend (estara-demo-api.onrender.com)
                                        │
   You / prospects ──► Netlify: dashboard ──/api──┘──► Neon: PostgreSQL (demo database)
   UptimeRobot pings the backend every 5 minutes, so it stays awake, and emails you if it is down.
```

## What each free service gives, and its limits (checked 2026-11-23)

| Part | Service | Card needed | Limits that matter |
|---|---|---|---|
| Website | Cloudflare Pages | No | 500 builds/month; commercial use allowed. Always on. |
| Backend | Render free web service | No | 512 MB, 0.1 CPU. **Sleeps after 15 min without traffic, about 1 min to wake.** 750 instance-hours/month per workspace (one service awake all month uses up to 744). 5 GB bandwidth, 500 build minutes. Render may restart it at any time. No shell. |
| Database | Neon free | No | 1 GB storage, 100 compute-hours/month. Sleeps after 5 min idle and wakes in under a second. Never paused for inactivity. |
| Dashboard | Netlify free | No | 300 credits/month (a production deploy costs 15). Over the limit, the site pauses until next month. It only rebuilds when `dashboard/` changes. |
| Keep-awake + alerts | UptimeRobot free | No | 5-minute checks, email alerts. |
| Voice | Vapi | Your choice | The free credits run out. Then switch account (section 9). |

**What this means honestly:** it costs ₹0, but nothing on free plans is guaranteed 24/7. Two
things can still go wrong. The backend can restart, and the first call after that may wait about
a minute; the website starts waking it the moment a visitor opens the page. And a call can't
start when the Vapi credits run out. The website itself stays up in both cases. **Never** add a
second always-on free service to the same Render workspace: two would use more than 750 hours,
and Render would suspend both until next month.

What changes compared with your local setup (all automatic, nothing to manage):
- No Redis and no worker. The login rate limit runs in memory, and the daily jobs (staleness
  check, data retention, demo-data refresh) run inside the backend. WhatsApp is not part of the
  public demo.
- No tunnel. Render gives a fixed https address. On every start the backend sets up the database,
  loads the demo homes if needed, and updates the Vapi assistant with that address.
- Calls are capped at 5 minutes (`DEMO_MAX_CALL_MINUTES`).

## 0. The repository

The code lives in the private GitHub repository **aniket13zzz/estara-pro-v2**, in your folder
`C:\Users\rijud\Documents\estara-pro-v2`. Keep the repository **private** (Settings → General →
Danger Zone → Change visibility). All the services below work with private repositories.

## 1. Accounts (all free, sign in with GitHub)

Create these accounts, each with **Continue with GitHub**: neon.com, render.com,
dash.cloudflare.com, netlify.com, uptimerobot.com. None asks for a card. If one does, stop and
skip that step: something has changed.

## 2. Database (Neon)

1. Neon → **New project**. Name `estara-demo`. Region **AWS Asia Pacific (Singapore)**. Create.
2. On the project page, click **Connect**. Turn **Connection pooling off** (ESTARA needs a direct
   connection). Copy the connection string. It looks like
   `postgresql://neondb_owner:...@ep-...ap-southeast-1.aws.neon.tech/neondb?sslmode=require...`
3. Keep it for step 3. It is a password, so paste it only into Render, never into chat.

You do not run anything here. The backend creates the tables and loads the 129 demo homes itself.

## 3. Backend (Render)

1. Render → **New** → **Blueprint** → pick `aniket13zzz/estara-pro-v2` → Render reads `render.yaml`.
2. It asks for the values marked secret. Fill them in:

| Variable | What to put |
|---|---|
| `DATABASE_URL` | the Neon string from step 2 |
| `DASHBOARD_ORIGIN` | for now `https://example.netlify.app`; you change it in step 4 |
| `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD` | the dashboard login you want (password: 12+ characters) |
| `SEED_STAFF_EMAIL` / `SEED_STAFF_PASSWORD` | a second login for a salesperson |
| `VAPI_API_KEY` | Vapi → API Keys → **private** key |
| `VAPI_WEBHOOK_SECRET` | the same value as in your local `.env` |
| `VAPI_ASSISTANT_ID` | the same value as in your local `.env` (the assistant the website uses) |
| `VAPI_SERVER_CREDENTIAL_ID` | the same value as in your local `.env` |

   To see your local values without printing anything else:
   `Select-String -Path .env -Pattern "^VAPI_(WEBHOOK_SECRET|ASSISTANT_ID|SERVER_CREDENTIAL_ID)="`
3. **Apply**. The first build takes about 5–10 minutes.
4. Open the service → **Logs**. You should see `[start] database: applying migrations`, then
   `Imported and approved units.csv: 129 rows`, then `Updated Vapi assistant ...`, then
   `[start] serving on port ...`. About 90 seconds later comes `ESTARA demo check` with PASS lines.
5. Copy the address at the top, for example `https://estara-demo-api.onrender.com`. Open
   `<that address>/api/v1/health`. You should see `"database":"ok"` and `"redis":"not_configured"`.

## 4. Dashboard (Netlify)

1. Netlify → **Add new project** → **Import an existing project** → GitHub → `estara-pro-v2`.
2. **Base directory**: `dashboard`. Leave the build command and publish directory as filled in
   (they come from `dashboard/netlify.toml`).
3. **Environment variables** → add `BACKEND_URL` = your Render address from step 3.5.
4. **Deploy**. Then **Site configuration → Change site name**, for example `estara-demo-dashboard`.
   Your dashboard is now `https://estara-demo-dashboard.netlify.app`.
5. Back in Render → your service → **Environment** → set `DASHBOARD_ORIGIN` to that address
   (no `/` at the end) → **Save, rebuild and deploy**.
6. Open the dashboard and sign in with `SEED_ADMIN_EMAIL`. **Properties** shows 19 projects and a
   yellow **Demo data** notice.

## 5. Website (Cloudflare Pages)

1. Cloudflare → **Workers & Pages** → **Create** → **Pages** tab → **Import an existing Git
   repository** → GitHub → `estara-pro-v2`. (Choose Pages, not Workers.)
2. Project name `estara-demo`. Production branch `main`.
3. Framework preset **None**. Build command `node tools/write-config.mjs`. Build output
   directory `public`. Root directory `demo-site`.
4. **Environment variables** (these replace `config.js`, so switching Vapi needs no code change):

| Variable | Value |
|---|---|
| `ESTARA_VAPI_PUBLIC_KEY` | Vapi → API Keys → **public** key (never the private one) |
| `ESTARA_ASSISTANT_ID` | the same as `VAPI_ASSISTANT_ID` on Render |
| `ESTARA_API_URL` | your Render address (the page wakes the backend with it) |
| `ESTARA_DEMO_ENABLED` | `true` |

5. **Save and Deploy**. Your demo is `https://estara-demo.pages.dev`.
6. The old Vercel site is no longer needed. Vercel's free plan is for non-commercial use only,
   and showing prospects counts as commercial. Vercel → project → Settings → **Delete Project**.

## 6. Protect the Vapi key (abuse and cost)

Vapi → **API Keys** → **Public API Keys** → **Add Key**: Allowed Origins
`https://estara-demo.pages.dev`, Allowed Assistants = your assistant → Create. Put this new public
key into Cloudflare `ESTARA_VAPI_PUBLIC_KEY` → **Deployments → Retry deployment**. Then delete the
old unrestricted public key. Copies of the site elsewhere can no longer use your credits.

The other protections are already on: calls are capped at 5 minutes; the backend accepts tool
calls only with the shared secret; the login is rate-limited; and the demo uses its own database
with synthetic data only.

## 7. Keep it awake (UptimeRobot)

UptimeRobot → **New monitor** → HTTP(s) → URL `<Render address>/api/v1/health/live` → interval
**5 minutes** → alert by email → Create. This keeps the backend from sleeping, and you get an
email if it goes down. It uses up to 744 of Render's 750 hours a month, so keep it the only
always-on Render service. The `/health/live` check does not touch the database, so Neon can sleep.

## 8. Test with your laptop shut down

On your phone (mobile data, not your Wi-Fi):
1. Open `https://estara-demo.pages.dev` → **Talk to Priya** → allow the microphone.
2. Say "I need a 3 BHK, anywhere around Kolkata, about 1 crore" → she names sample projects.
3. Ask for a visit, give a name → she says the team will confirm.
4. Open the dashboard on the phone → **Calls** and **Leads** show the call and the visit request.

To see the full check at any time: sign in to the dashboard, then open
`https://estara-demo-dashboard.netlify.app/api/v1/diagnostics`. It shows the data, the address,
the assistant, and Vapi's record of the last call, with a fix for anything that fails.

## 9. Updating, switching Vapi accounts, pausing

**Updating:** `git push` is all you do. Render redeploys when `backend/`, `client/` or `deploy/`
change; Netlify when `dashboard/` changes; Cloudflare on every push.

**New Vapi account** (when credits run out):
1. In the new account, create the assistant (model, voice, transcriber) as in `docs/demo.md` 1d.
2. Create the Bearer credential with the **same** secret (`VAPI_WEBHOOK_SECRET`) → note its id.
3. Create a restricted public key (section 6).
4. Render → Environment: `VAPI_API_KEY`, `VAPI_ASSISTANT_ID`, `VAPI_SERVER_CREDENTIAL_ID` → Save.
   On restart the backend writes Priya's instructions and tools into the new assistant.
5. Cloudflare → Settings → Variables: `ESTARA_VAPI_PUBLIC_KEY`, `ESTARA_ASSISTANT_ID` → Deployments
   → **Retry deployment**.

**Emergency stop:** Cloudflare → `ESTARA_DEMO_ENABLED` = `false` → Retry deployment. The call
button then says the demo is paused, and the website stays up. For a faster stop, delete the
public key in Vapi. The dashboard's pause switch also hands every call to the team.

## 10. Troubleshooting

| You see | Cause | Fix |
|---|---|---|
| Render deploy fails, log shows `Invalid environment settings` | a variable is missing or short | Render → Environment: check the one named in the log (`SESSION_SECRET` is generated for you) |
| Log shows `migrations failed` | wrong `DATABASE_URL` | paste the Neon string again, with pooling **off** |
| Priya says she can't get the details | Vapi can't reach the backend | open `/api/v1/diagnostics` (section 8); the FAIL line says which link |
| First call after a long quiet time is slow | the backend was asleep | check UptimeRobot is running (section 7) |
| "Not set up yet" on the website | Cloudflare variables missing | section 5.4, then Retry deployment |
| Call won't start | Vapi credits used up, or a wrong public key | Vapi dashboard → Billing; section 9 |
| Dashboard login says cross-origin | `DASHBOARD_ORIGIN` doesn't match the Netlify address | section 4.5 |
| Netlify build says "Set BACKEND_URL" | the variable is missing | section 4.3 |
| Everything stopped on the 1st of the month | Render hours or Netlify credits ran out | check that only one Render service is always on; it resumes next month |
