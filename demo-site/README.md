# Demo website (Sunrise Developers + Priya)

A public page you show a prospect. They click **Talk to Priya** and have a real voice call with
ESTARA in the browser. They never see Docker, PowerShell or the tunnel: those run on your PC.

Everything on the page is FAKE sample data (the same ten Kolkata projects that `seed` loads).

## What is in this folder

- `public/`: the website. This is the only folder you deploy. `app.js` is already built.
- `public/config.js`: the two values you fill in (below).
- `src/`, `tools/`, `package.json`: only needed if you change the code or the sample data.

## 1. Fill in config.js (once)

Open `demo-site\public\config.js` in Notepad and fill in:

- `vapiPublicKey`: Vapi dashboard > **API Keys** > the **Public** key.
  It is made for websites, so it is fine to put here. Never put the **private** key,
  `VAPI_WEBHOOK_SECRET` or anything from `.env` in this file.
- `assistantId`: the same value as `VAPI_ASSISTANT_ID` in your `.env`.

Save it. Then push to GitHub:

```powershell
cd C:\Users\rijud\Documents\estara-pro-v1
git add demo-site
git commit -m "Demo site settings"
git push
```

## 2. Deploy on Vercel (free, once)

1. Go to vercel.com and sign in with GitHub.
2. **Add New > Project**, pick your `estara-pro` repository, click **Import**.
3. **Root Directory**: click **Edit** and choose `demo-site/public`.
4. **Framework Preset**: `Other`. Leave Build Command and Output Directory empty.
5. Click **Deploy**. You get an address like `https://estara-pro-xxxx.vercel.app`.

You should see the dark skyline page. Every later `git push` updates the site by itself.

Safety: if your Vapi public key has an "allowed origins" (or allowed domains) setting,
put your Vercel address there, so the key only works from your site.
The page has `noindex`, so search engines do not list it. Share the link only with prospects.

## 3. Before every demo (about 3 minutes)

The website is always online, but Priya can only answer when ESTARA is running on your PC.

```powershell
cd C:\Users\rijud\Documents\estara-pro-v1
docker compose up -d
```

In a second PowerShell window:

```powershell
cloudflared tunnel --url http://localhost:8000
```

Copy the new `https://....trycloudflare.com` address, put it in `.env` as `PUBLIC_BASE_URL`
(Notepad, save), then back in the first window:

```powershell
docker compose up -d backend
docker compose run --rm --no-deps backend python -m app.channels.vapi.sync
```

You should see the sync succeed. Open your Vercel address, click **Talk to Priya**, allow the
microphone, and say hello. Leave both windows open during the demo.

After the demo: `Ctrl+C` in the tunnel window, then `docker compose stop`.

If you click Talk to Priya and see **Not set up yet**, `config.js` is empty on the live site
(fill it in and push). If the call connects but Priya cannot answer questions about homes,
the tunnel or sync step was skipped.

## Changing the code or data (optional)

Needs Node.js. From `demo-site`:

```powershell
npm install
python tools\make_data.py ..\backend\sample_data\kolkata src\data.js   # rebuild the project list
npm run build   # rebuild public/app.js
```

Commit `public/app.js` after building; Vercel serves the files as they are.

## One command before each demo (Windows)

From the project folder, with Docker Desktop running:

```powershell
powershell -ExecutionPolicy Bypass -File .\start-demo.ps1
```

It starts ESTARA, starts the tunnel in the background, writes the new address into `.env`,
updates the Vapi assistant(s) set in `.env` (A: `VAPI_ASSISTANT_ID`, B: `VAPI_ASSISTANT_ID_B`) and
checks ESTARA answers through the tunnel. When it prints READY, open the website.
After the demo: `powershell -ExecutionPolicy Bypass -File .\stop-demo.ps1`.
