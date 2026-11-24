# Demo: call the agent and see the lead on the dashboard

Zero cost: free Vapi account, free Gemini key, a free tunnel, sample data. Windows commands are
for PowerShell in the `estara-pro` folder, with Docker Desktop running.

**What you will show:** someone talks to the agent in Hindi or English, asks for a 2 BHK under
80 lakh, raises a price concern and agrees to a visit. On the dashboard you then see the lead,
the call summary, the matched property and the visit request, and confirm the visit as staff.

---

## 1. One-time setup (about 30 minutes)

### 1a. Start ESTARA with sample data

```powershell
docker compose up -d --build --wait
```

On every start the backend updates the database and loads the 129 synthetic demo homes by itself
(and reloads them weekly so prices never go stale). It needs `SEED_ADMIN_EMAIL`,
`SEED_ADMIN_PASSWORD`, `SEED_STAFF_EMAIL` and `SEED_STAFF_PASSWORD` in `.env`; if one is missing,
`docker compose logs backend` says which. It never loads them in production or over real
inventory. To reload by hand: `docker compose run --rm --no-deps backend python -m app.seed`.

Sign in at http://localhost:3000 as `admin@example.com` with your `SEED_ADMIN_PASSWORD`.

### 1b. A free tunnel, so Vapi can reach your PC

Vapi's servers must reach your backend over https. Cloudflare's quick tunnel is free and needs no
account:

```powershell
winget install --id Cloudflare.cloudflared -e
cloudflared tunnel --url http://localhost:8000
```

Leave that window open. It prints an address like `https://something-random.trycloudflare.com`.
That address changes every time you start the tunnel; after a restart, update `PUBLIC_BASE_URL`
and repeat step 1e.

### 1c. Vapi account and keys

1. Sign up at vapi.ai (free credit for testing).
2. **Dashboard → API Keys**: copy the **private** key and the **public** key.
3. Make a long random secret for ESTARA:
   `docker compose run --rm --no-deps backend python -c "import secrets; print(secrets.token_urlsafe(32))"`
4. **Integrations → Server Configuration → Add Custom Credential**: Authentication Type
   **Bearer Token**, Token = your secret, Header Name `Authorization`, Include Bearer Prefix
   **on**. Save, and find its id (starts with `cred_`).

### 1d. Create Sara in Vapi (model, voice, listener)

ESTARA uses Vapi's own model (`voice.mode: native` in `client\client.config.yaml`, ADR 0014):
Vapi's model talks, and every fact, price, slot and booking comes from ESTARA's tools.

1. **Assistants → Create Assistant** (blank). Name it, e.g. "Sara".
2. **Model**: choose the model (for example GPT-6 Luna). **Transcriber**: one that handles
   Hindi and English (for example Soniox multilingual). **Voice**: Cartesia Sonic, a voice that
   sounds natural in both languages. Publish.
3. Copy the assistant's **ID** (shown under its name).
4. **Do not add tools by hand** and do not type a system prompt: the sync in 1e adds ESTARA's
   10 tools and Sara's instructions. Tools made in the **Tools** page ("API Request") cannot
   tell ESTARA which caller they are for, so they are removed from the assistant by the sync.

Put everything in `.env` (Notepad: `notepad .env`), never in chat:

```
VAPI_API_KEY=<private key>
VAPI_PUBLIC_KEY=<public key>
VAPI_WEBHOOK_SECRET=<your secret>
VAPI_SERVER_CREDENTIAL_ID=<cred_... id>
VAPI_ASSISTANT_ID=<the assistant ID from step 3>
PUBLIC_BASE_URL=https://something-random.trycloudflare.com
```

### 1e. Connect ESTARA's tools to Sara

```powershell
docker compose up -d --force-recreate --wait
docker compose run --rm --no-deps backend python -m app.channels.vapi.sync
```

You should see `Updated Vapi assistant ... (native mode, model ... kept)` and the list of
tools: `get_customer_context, search_properties, get_property_details, get_commercial_info,
search_knowledge, save_requirements, get_visit_slots, request_site_visit, transfer_to_human,
schedule_follow_up` + `transferCall`. In Vapi, the assistant now shows these tools and Sara's
instructions.

What you may change in Vapi: the model, voice and transcriber, any time. What the sync owns:
the instructions, tools, greeting, server address and call length. Change those through
`client\` (scripts, config) and run the sync again. Run it again also after every tunnel
restart (new `PUBLIC_BASE_URL`).

**Other mode (`voice.mode: custom_llm`):** ESTARA writes every reply itself, which lets it
check every number before it is spoken, but answers are slower. Then also set Vapi's
**Integrations → Custom LLM** API key to your secret, put the Cartesia and transcriber values
in `voice.tts` / `voice.stt` in the config, leave `VAPI_ASSISTANT_ID` empty for the first sync,
and put the id it prints into `.env`.

---

## 2. The demo (15 minutes)

1. Open http://localhost:3000/test-call and click **Start call**. Allow the microphone.
2. Speak as a buyer: *"नमस्ते, मुझे न्यू टाउन में 2 BHK चाहिए, बजट अस्सी लाख तक।"*
3. Raise a concern: *"थोड़ा महँगा है।"* The agent turns it into a reason to visit and offers two
   slots.
4. Agree: *"Saturday ठीक है, मेरा नाम आशा है।"* The agent says the visit is requested and the team
   will confirm.
5. Trust test: *"कोई Diwali offer है?"* If it is not in the approved data, the agent says the team
   will confirm. It never invents an offer.
6. *"Manager से बात कराओ."* The agent hands over (see live check 3 below).
7. End the call. Open **Leads**: the new lead with budget, area and BHK. Open it: requirements,
   matched property, the conversation, and the visit request.
8. Open **Calls** after about a minute: the English summary, transcript and recording.
9. Open **Visits** and click **Confirm**. The lead moves to "visit confirmed".

---

## WhatsApp (free test number)

Meta gives every developer app a free **test number** that can message up to a few phone
numbers you register. That is enough for a demo.

1. At developers.facebook.com create an app (type **Business**) and add **WhatsApp**.
2. **WhatsApp → API Setup**: copy the **temporary access token** and the **Phone number ID**.
   Under "To", add and verify your own phone and the client's phone (before the meeting).
3. **App settings → Basic**: copy the **App secret**.
4. Put them in `.env` (Notepad), plus a verify token you make up:

   ```
   WHATSAPP_ACCESS_TOKEN=<temporary access token>
   WHATSAPP_PHONE_NUMBER_ID=<phone number id>
   WHATSAPP_APP_SECRET=<app secret>
   WHATSAPP_VERIFY_TOKEN=<any long random text>
   ```

5. Restart: `docker compose up -d --force-recreate --wait`
6. **WhatsApp → Configuration → Webhook**: callback URL
   `<PUBLIC_BASE_URL>/api/v1/webhooks/whatsapp`, verify token as in `.env`, then **Verify and
   save**, and subscribe to the **messages** field.
7. Send "Hi" from your registered phone to the test number. The agent answers.

**Visit messages (requested, confirmed, reminder).** When the customer is not inside WhatsApp's
24-hour window (for example they booked by phone), Meta only allows **approved templates**.
Print the exact templates to submit:

```powershell
docker compose run --rm --no-deps backend python -m app.channels.whatsapp.templates
```

Submit each one in **WhatsApp Manager → Message templates** with the same name and language
(category Utility). Until they are approved, notices outside the 24-hour window are refused by
Meta and logged; replies inside the window work without templates. On a call the agent asks
before sending anything on WhatsApp; a customer who replies STOP (or बंद करो) gets nothing more.

Note: the temporary access token expires after about a day. For a lasting setup create a
system-user token in Meta Business Settings (before first customer).

---

## 3. Live checks (record the results in docs/progress.md)

These cannot be proven without a real call. Each has a safe fallback.

1. **Tools are called.** During a test call ask about a 2 BHK and a price. Open **Leads**: the
   lead shows the budget, area and BHK, and **Calls** shows the call. If nothing appears, check
   `docker compose logs backend --tail 80` for `401` (secret or credential mismatch) or
   `tool-calls without call.id` (send that line to the developer).
2. **Summary, recording and "check numbers".** After the call ends, the Calls page shows the
   summary and recording. If it shows **check numbers**, Sara said a number that no approved
   data gave: open the call, listen, and tell the developer what she said.
3. **Transfer to a person.** Ask for a manager during working hours: Sara says the transfer line
   and Vapi transfers the call to `escalation.human_transfer_number`. If nobody answers, Sara
   stays on the line and offers a callback. Note the result.
4. **Latency.** Note how long Sara pauses before answering, especially after a price question
   (a tool call). Vapi's Assistant page shows its own latency estimate.
5. **Hindi quality.** Note any word the transcriber mishears (numbers, area names) and any
   reply that sounds unnatural.
6. **WhatsApp round trip.** "Hi" from your phone gets an answer; the lead page shows the call
   and the WhatsApp messages under one customer. After a phone booking where you said yes to
   WhatsApp, the "visit requested" message arrives within a minute (needs the approved
   template), and the confirmation arrives within a minute of clicking Confirm.

## 4. A real phone number (before first customer)

Not needed for the demo. When a client pays: buy or connect an Indian number in Vapi (call
forwarding from the client's sales number, or SIP via an Indian provider; KYC applies), set the
number's **server URL** to `<PUBLIC_BASE_URL>/api/v1/webhooks/vapi/events` with the same
credential, and leave its assistant empty. ESTARA then answers each call with the assistant, or
forwards straight to the human number when the agent is paused. See
`docs/before-first-customer.md`.

## Troubleshooting

| What you see | Why | Fix |
| --- | --- | --- |
| Test call page: "Voice is not set up yet" | A setting is missing | It lists which; see 1c–1e |
| Sync: "VAPI_ASSISTANT_ID is not set" | Native mode needs the assistant made in Vapi first | Step 1d |
| Sync: "choose a model for this assistant" | The assistant still uses ESTARA as custom LLM | In Vapi pick a model (Assistant → Model), sync again |
| Sync: "Not ready" | `PUBLIC_BASE_URL` not https, or the secret is missing | Steps 1b and 1c |
| Sara answers but nothing appears on Leads | Tool calls are refused | The credential token must equal `VAPI_WEBHOOK_SECRET`; run the sync after fixing |
| Sara says "the team will confirm" to everything | Tools fail or are missing | Check the backend log; run the sync again |
| Every call goes straight to a person | Agent paused, voice not ready, or the AI failed | Check the backend log: `docker compose logs backend --tail 50` |
