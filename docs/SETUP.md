# Setting up Jerry

Jerry is a multi-agent assistant running on a local n8n instance. A Master Agent
takes your chat message and delegates to three specialists — Email, Calendar and
Meeting — each with its own Gemini model, its own memory, and its own tools.

Everything lives inside this folder. Deleting it removes Jerry completely.

---

## 0. Prerequisites

n8n needs an LTS Node. Install one, keg-only so it does not shadow whatever
Node you already use — the scripts call it by absolute path:

```bash
brew install node@22
```

On a fresh checkout, `./scripts/setup.sh` then installs n8n into `runtime/`
(a few minutes, about 1 GB). Later runs skip it.

> On Apple Silicon, Homebrew installs to `/opt/homebrew` rather than
> `/usr/local`. Update `NODE22_BIN` in `scripts/start-n8n.sh` and `NODE22` in
> `scripts/setup.sh` if `brew --prefix` reports a different path.

What is already done in this checkout: Node 22 and n8n are installed, and
`workflows/jerry.workflow.json` has been generated, validated and imported.
What remains is supplying your own API keys and connecting Google.

---

## 1. Google Gemini API key

Powers all four agents.

1. Go to <https://aistudio.google.com/apikey>
2. **Create API key** → pick a Google Cloud project (or let it make one)
3. Copy the key into `config/.env` as `GEMINI_API_KEY`

The free tier is enough for personal use. Jerry uses `models/gemini-3.5-flash`,
which is free of charge and the strongest of the free models at tool calling. To
change it, edit `GEMINI_MODEL` in `scripts/lib/nodes.mjs` and rebuild.

---

## 2. Google OAuth — for Gmail and Calendar

One OAuth client covers both. This is the fiddliest part; the scopes differ per
n8n credential, not per Google client, so you only do it once.

1. <https://console.cloud.google.com/> → create or select a project.

2. **APIs & Services → Library** → enable both:
   - **Gmail API**
   - **Google Calendar API**

3. **APIs & Services → OAuth consent screen**. Google has renamed this to
   **Google Auth Platform**; if you land on a page saying "Google Auth Platform
   not configured yet", click **Get started** and work through its four steps:
   - *App Information* — App name `Jerry`, user support email = your address
   - *Audience* — **External** (unless you have a Workspace org, then Internal)
   - *Contact Information* — your address again
   - *Finish* — agree to the User Data Policy, then **Create**

   The old single consent-screen page is now split across the left-hand nav:
   **Branding** (name and logo), **Audience** (user type, test users, publishing
   status), **Data Access** (scopes), **Clients** (OAuth client IDs).

4. **Audience → Test users → + Add users**: add your own Google account.
   While the app is in Testing only listed test users can authorise, and refresh
   tokens expire after 7 days — fine for personal use. Publishing removes the
   expiry but shows Google's unverified-app warning.

5. **Clients → + Create client**
   - Application type: **Web application**
   - Name: anything, e.g. `Jerry local`
   - Under **Authorized redirect URIs**, click **+ ADD URI** and paste exactly:

     ```
     http://localhost:5678/rest/oauth2-credential/callback
     ```

     Google matches this as an exact string. `http` is allowed here because
     localhost is explicitly exempt from the HTTPS rule. `127.0.0.1` is a
     *different* URI — if you use it, register it separately. If you change
     `N8N_PORT`, the port in this URI must change to match.

6. Copy the client ID and client secret into `config/.env` as
   `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`. If you dismiss the
   popup, get them back from **Clients → Jerry local**.

---

## 3. Fireflies API key

Powers the Meeting Agent's transcript tools.

1. <https://app.fireflies.ai/integrations/custom/fireflies>
2. Copy the API key into `config/.env` as `FIREFLIES_API_KEY`

**This one is optional.** Leave `FIREFLIES_API_KEY` blank and everything else
still works — `setup.sh` just skips that credential. The Meeting Agent will be
on the canvas but will error if the Master Agent routes to it, so avoid asking
about past meetings until you have a key. If you never want it, delete the third
entry from the `groups` array in `scripts/build-workflow.mjs` and rebuild.

Note the API is not available on every Fireflies plan — if the page above shows
no key, that is a plan restriction rather than anything wrong on your side.

Rate limits: 50 requests/day on Free, 500/day on Pro, 60/minute on Business and
Enterprise. A few fields — `audio_url`, `video_url`, `analytics` — need Pro or
higher and return a `paid_required` error otherwise. Jerry does not request them.

---

## 4. Apply it

```bash
cp config/.env.example config/.env    # if you have not already
# fill in the four values, then:
./scripts/setup.sh
```

`setup.sh` rebuilds the workflow, validates it against the installed n8n,
imports it, and seeds the credentials. It is safe to re-run at any time — the
workflow and credentials have fixed ids, so it updates in place rather than
creating duplicates.

Stop n8n before running it; the CLI writes to the same SQLite database.

---

## 5. Connect Google

API keys can be seeded from a file. OAuth cannot — it needs a browser round-trip.

```bash
./scripts/start-n8n.sh
```

Open <http://localhost:5678>, create the local owner account when prompted (it
never leaves your machine), then:

- **Credentials → Jerry — Gmail → Connect my account** → pick your Google
  account → you will see an "unverified app" warning, choose **Advanced →
  Go to … (unsafe)** → **Allow**
- Repeat for **Jerry — Google Calendar**

Both should end up showing "Account connected".

---

## 6. Talk to Jerry

Open **Jerry - Multi Agent Assistant System** and click **Chat** at the bottom.

Things that exercise each agent:

| Ask | Path |
|---|---|
| "What unread email do I have from this week?" | Master → Email |
| "Am I free Tuesday afternoon?" | Master → Calendar |
| "Summarise my last meeting" | Master → Meeting |
| "Draft a reply to Priya's last email agreeing to Thursday" | Master → Email (search, then draft) |
| "Find the email about the budget review and book 30 minutes with the sender tomorrow" | Master → Email → Calendar |

Jerry asks you to confirm before sending mail, deleting anything, or inviting
other people. Searching and reading happen without asking.

---

## Troubleshooting

**`redirect_uri_mismatch`** — the URI in Google Cloud does not byte-match what
n8n sends. Check the port, the trailing path, and `localhost` vs `127.0.0.1`.

**Google asks you to re-authorise every week** — expected while the OAuth
consent screen is in Testing. Publish the app to stop it.

**"Could not get parameter" / red node on the canvas** — run
`node scripts/validate-workflow.mjs`. It resolves every node type and version
against the n8n in `runtime/` and reports what is wrong.

**"model not found" from every agent** — Jerry defaults to
`models/gemini-3.5-flash`. Which models your key can reach depends on your
Google Cloud project. Open any **Google Gemini Chat Model** node and use the
**Model** dropdown — it lists exactly what your key supports. Pick one, then set
`GEMINI_MODEL` in `scripts/lib/nodes.mjs` to match so a rebuild does not undo
it. `models/gemini-3-flash-preview` is n8n's own default and a safe fallback.

**Gemini quota errors** — the free tier has per-minute limits and Jerry can make
several model calls per message, since each sub-agent has its own model. Wait a
minute, or attach billing to the Google Cloud project.

**The Meeting Agent returns nothing** — Fireflies only has meetings its notetaker
actually joined. Check <https://app.fireflies.ai> shows recordings first.

**n8n warns that running outside Docker is deprecated** — cosmetic for now.
Future n8n versions will require the Docker image. All state is in
`runtime/.n8n`, so migrating later is a copy.
