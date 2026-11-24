# Deploy ESTARA Pro for a client

One deployment per client, on the client's own server and accounts. Do
[new-client-setup.md](new-client-setup.md) first (config, scripts), and every item in
[before-first-customer.md](before-first-customer.md) before real customers call.

You work from your Windows PC and connect to the server with `ssh` (built into Windows). Server
commands below are typed **on the server** after `ssh`.

## 1. What you need

- A Linux server (Ubuntu 24.04, 2 vCPU, 4 GB RAM, 40 GB disk is enough for one developer),
  under the client's account, with ports **80** and **443** open and nothing else public.
- A domain or subdomain for ESTARA, e.g. `ai.clientname.in`, with a DNS **A record** pointing at
  the server's IP. Caddy gets the HTTPS certificate for it automatically.
- The client's own keys: Gemini (paid), Vapi, WhatsApp (Meta). Never use yours.

## 2. Install and start

On the server:

```bash
curl -fsSL https://get.docker.com | sh
git clone <the client's private repository> estara && cd estara
cp .env.example .env
nano .env
```

Fill in `.env` (never paste keys into chat or email):

| Variable | Value |
| --- | --- |
| `DOMAIN` | `ai.clientname.in` |
| `POSTGRES_PASSWORD` | a long random password |
| `SESSION_SECRET` | `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `GEMINI_API_KEY` | the client's paid key |
| `VAPI_*`, `WHATSAPP_*` | see sections 4 and 5 |
| `SEED_*` | leave empty: no sample users on a live server |

`ENVIRONMENT`, `COOKIE_SECURE`, `DASHBOARD_ORIGIN` and `PUBLIC_BASE_URL` are set from `DOMAIN`
by `docker-compose.prod.yml`; you do not need to set them.

```bash
alias dc='docker compose -f docker-compose.yml -f docker-compose.prod.yml'
dc up -d --build --wait
dc run --rm --no-deps backend alembic upgrade head
dc run --rm --no-deps backend python -m app.core.config
dc run --rm --no-deps backend python -m app.auth.cli create-user \
  --email owner@clientname.in --name "Owner" --role client_admin
```

The last command asks for the password (12+ characters). Create sales users the same way with
`--role sales_staff`. Do **not** run `app.seed` on a live server (it loads fake sample data).

Open `https://ai.clientname.in`, sign in, check **Status** is all green, then upload and approve
the client's data in the **Data Center**.

## 3. Check it from outside

From your PC:

```powershell
curl.exe https://ai.clientname.in/api/v1/health
curl.exe -s -o NUL -w "%{http_code}" https://ai.clientname.in/api/v1/leads
```

The first answers `{"status":"ok",...}`; the second must say `401` (login required).

## 4. Vapi (the phone agent)

In the client's Vapi account (steps as in [demo.md](demo.md) 1c–1e, with the client's domain
instead of a tunnel):

1. Create the Bearer credential with the client's secret (`VAPI_WEBHOOK_SECRET`). Put
   `VAPI_API_KEY`, `VAPI_PUBLIC_KEY`, `VAPI_WEBHOOK_SECRET` and `VAPI_SERVER_CREDENTIAL_ID` in
   `.env`.
2. Create the assistant in Vapi and choose the model, transcriber (Hindi + English) and voice.
   Put its ID in `.env` as `VAPI_ASSISTANT_ID`. Add no tools by hand.
3. Connect ESTARA's tools and instructions:

   ```bash
   dc up -d --force-recreate --wait
   dc run --rm --no-deps backend python -m app.channels.vapi.sync
   ```

   Run the sync again after changing scripts, the transfer number or the domain.
4. **Phone number**: in Vapi, **Phone Numbers** → add the client's Indian number (imported from
   their telephony/SIP provider, or forwarding from their sales line). Set its **Server URL** to
   `https://ai.clientname.in/api/v1/webhooks/vapi/events` with the same credential, and leave
   **Assistant** empty. ESTARA then picks the assistant on every call, or sends the call straight
   to `escalation.human_transfer_number` when the agent is paused or a limit is reached.
5. Make a test call from your own phone, then open **Calls** in the dashboard.

Recordings are stored by Vapi. ESTARA removes its links after `retention.recording_days`; set
the same retention (or deletion) in the client's Vapi account. In the client's Vapi account,
also set a spending limit: Vapi bills the model inside its per-minute price, and ESTARA's
`usage_limits` only see call minutes after each call ends.

Check the dashboard's **Calls** page during the pilot: a call marked **check numbers** means the
agent said a number that no approved data gave (ADR 0014). Listen to it and correct the customer.

## 5. WhatsApp

In the client's Meta Business account, with their **verified** WhatsApp Business number:

1. App → WhatsApp → API Setup: `WHATSAPP_PHONE_NUMBER_ID`. Create a **permanent** access token
   for a system user (Business Settings → System users) with `whatsapp_business_messaging`:
   `WHATSAPP_ACCESS_TOKEN`. App settings → Basic: `WHATSAPP_APP_SECRET`. Choose
   `WHATSAPP_VERIFY_TOKEN`. Put all four in `.env` and run `dc up -d --force-recreate --wait`.
2. WhatsApp → Configuration → Webhook: URL `https://ai.clientname.in/api/v1/webhooks/whatsapp`,
   the same verify token, **Verify and save**, subscribe to **messages**.
3. Submit the templates printed by
   `dc run --rm --no-deps backend python -m app.channels.whatsapp.templates`
   (same names and languages, category Utility). Visit messages outside the 24-hour window
   start working once Meta approves them.

## 6. The client's website

Add these buttons to the client's site (their web developer can paste this anywhere). Replace
the two numbers: the Vapi phone number, and the WhatsApp Business number in international
format without `+` or spaces.

```html
<div style="display:flex;gap:12px;flex-wrap:wrap">
  <a href="tel:+919800000000"
     style="padding:12px 20px;border-radius:8px;background:#0b5394;color:#fff;text-decoration:none">
    Call us
  </a>
  <a href="https://wa.me/919800000000?text=Hi%2C%20I%27m%20interested%20in%20your%20projects"
     target="_blank" rel="noopener"
     style="padding:12px 20px;border-radius:8px;background:#1f8f4e;color:#fff;text-decoration:none">
    WhatsApp us
  </a>
</div>
```

Both reach the agent. Nothing is embedded in the site and no keys go into it.

## 7. Backups

The `backup` service saves the database and uploaded files every day at `BACKUP_TIME_UTC`
(default 21:00 UTC = 02:30 India) into the `backups/` folder on the server, keeping
`BACKUP_KEEP_DAYS` (default 14) days. Files are readable only by root.

```bash
dc run --rm backup now                    # one backup right now
ls -lh backups/                           # estara-db-*.dump and estara-uploads-*.tar.gz
dc logs backup --tail 20                  # "backup ok" every day
```

**Copy the backups off the server** to storage the client owns (a server that dies takes its
`backups/` folder with it). For example, from your PC once a week:
`scp -r root@ai.clientname.in:estara/backups D:\estara-backups\clientname`
or the client's own off-site storage tool.

**Restore** (replaces the current data; stop the agent first):

```bash
dc stop backend worker dashboard caddy
dc run --rm --entrypoint sh backup /scripts/restore.sh \
  estara-db-YYYYMMDD-HHMM.dump estara-uploads-YYYYMMDD-HHMM.tar.gz
dc run --rm --no-deps backend alembic upgrade head
dc up -d --wait
```

Test a restore once after go-live, so you know it works before you need it.

## 8. Updates

When a new template version is released (a git tag such as `v1.0.1`):

```bash
dc run --rm backup now
git fetch --tags && git merge v1.0.1     # only client/ and .env are client-specific
dc up -d --build --wait
dc run --rm --no-deps backend alembic upgrade head
```

Changing `client/client.config.yaml` or a script only needs `dc up -d --force-recreate --wait`
(and the Vapi sync if the voice, greeting or transfer number changed). An invalid config stops
the backend with a list of problems: `dc logs backend --tail 50`.

## 9. Everyday operation

- **Pause** the agent from the dashboard **Home** page (admins). Calls then go straight to the
  team's number; WhatsApp gets the "team will contact you" message.
- **Usage** against the monthly limits is on the Home page. When a limit is reached, calls go to
  the team's number until the month ends or the limit in config is raised.
- **Logs**: `dc logs backend --tail 100`. Phone numbers, emails and keys are hidden in logs.
- **Health**: `https://ai.clientname.in/health` (dashboard Status page).
