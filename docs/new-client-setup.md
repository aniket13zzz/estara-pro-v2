# New client setup

One copy of ESTARA per client. You change only **`client/`**, **`.env`** and the **data uploaded
in the dashboard**. If a client need seems to require a code change, stop: it belongs in config,
or the template needs fixing for everyone.

Commands are for PowerShell on your Windows PC with Docker Desktop running.

## 1. Make the client's copy (10 minutes)

On GitHub, create a **private** repository for the client (e.g. `estara-greenvalley`), then:

```powershell
git clone https://github.com/<you>/estara-pro.git estara-greenvalley
cd estara-greenvalley
git checkout v1.0.0
git remote rename origin template
git remote add origin https://github.com/<you>/estara-greenvalley.git
git switch -c main
git push -u origin main
```

Template updates later come from `template` (see [deploy.md](deploy.md), "Updates").

## 2. Edit `client/client.config.yaml` (30 minutes)

Open it in Notepad (`notepad client\client.config.yaml`). Every field has a comment. The ones
that always change:

| Field | Example |
| --- | --- |
| `company.name` | `Green Valley Homes` |
| `agent.name`, `agent.gender` | `Arjun`, `male` (Hindi verb forms follow this; pick a matching voice) |
| `agent.default_language` | `en` or `hi`: the language the agent greets in first |
| `sales_team` | name, phone (`+91...`), email of each salesperson |
| `escalation.human_transfer_number` | the number callers are transferred to (`+91...`) |
| `escalation.working_hours` | days and hours a person can take a transfer |
| `site_visits.days`, `slots`, `slot_capacity` | when visits can happen and how many per slot |
| `usage_limits` | monthly call minutes and AI spend before calls go to a person |
| `voice` | left `null` until Vapi is set up ([deploy.md](deploy.md) section 4) |

All phone numbers in `+91XXXXXXXXXX` format.

## 3. Rewrite the scripts (1–2 hours)

`client/scripts/en/` and `client/scripts/hi/` hold every word customers hear or read. Same file
names in both folders. `{{company_name}}` and `{{agent_name}}` are filled in automatically.

- `greeting.md`, `recording_disclosure.md`, `ai_disclosure.md`: the first thing callers hear.
  **A lawyer should review the disclosure wording.**
- `sales_script.md`: how the agent qualifies and wins the site visit; add the client's selling
  points (never prices or dates: those come only from approved data).
- `objections/*.md`: answers to price, location, competitor and "I'll think about it".
- `whatsapp/*.md`: WhatsApp templates (submit them to Meta, [deploy.md](deploy.md) section 5).
- The rest are short lines for transfers, after hours, pauses and fallbacks.

Hindi is natural spoken Hinglish in Devanagari. If `agent.gender` is `male`, change the agent's
own verb forms in the Hindi scripts (`करूँगी` → `करूँगा`, `बताती हूँ` → `बताता हूँ`). Have a
native Hindi speaker read them.

## 4. Check it

```powershell
Copy-Item .env.example .env
notepad .env        # local passwords and SESSION_SECRET (see the comments in the file)
docker compose up -d --build --wait
docker compose run --rm --no-deps backend python -m app.core.config
```

The last command prints `Client config OK: <company>, ...` or a numbered list of exactly what is
wrong (missing script file, bad phone number, unknown field). Fix and run it again.

Then try it with fake data before the client sees it:

```powershell
docker compose run --rm --no-deps backend alembic upgrade head
docker compose run --rm --no-deps backend python -m app.seed
docker compose run --rm backend python -m app.brain.cli --lang hi
```

Talk to the agent as a buyer in Hindi and English (needs `GEMINI_API_KEY` in `.env`). Check it
uses the client's name, the agent's name and gender, and offers the configured visit slots.

## 5. Commit and deploy

```powershell
git add client
git commit -m "Green Valley Homes: config and scripts"
git push
```

`.env` is never committed. Then follow [deploy.md](deploy.md) on the client's server, upload the
client's real data in the Data Center, and work through
[before-first-customer.md](before-first-customer.md).
