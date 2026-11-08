# Jerry

A private inbox assistant that reads your email on your own machine and sorts it into a digest: what needs you, what's worth knowing, and what can be set aside. Mail is classified by a local model through Ollama, so it never leaves the computer.

This repo has two parts:

1. **The Jerry app** (`src/jerry/`): a macOS menu-bar app with a local web dashboard.
2. **An n8n multi-agent assistant** (`workflows/`, `scripts/`, `runtime/`): a Master Agent that delegates to Email, Calendar and Meeting agents on Gemini. Setup is in [docs/SETUP.md](docs/SETUP.md).

## Stack

- Python 3.12, FastAPI + Uvicorn (dashboard on `http://127.0.0.1:8765`, override with `JERRY_PORT`), SQLAlchemy, httpx, pystray
- Ollama for local classification (Jerry offers to install it and download a model on first run)
- Mail connectors: Gmail (OAuth with PKCE and a loopback redirect) or any IMAP server with an app password
- Secrets in the macOS Keychain; data in `~/Library/Application Support/Jerry`
- n8n 2.x on Node 22 for the multi-agent workflow
- PyInstaller for the `.app` bundle (`packaging/`)

## Run it

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"

.venv/bin/jerry            # menu-bar app
.venv/bin/jerry --serve    # dashboard only, for development
.venv/bin/jerry --run      # one digest, then exit
```

Install as an app that starts at login (macOS):

```bash
./install.sh   # builds Jerry.app, copies it to ~/Applications, adds a LaunchAgent
```

For the n8n assistant, copy `config/.env.example` to `config/.env`, fill in the keys, then follow [docs/SETUP.md](docs/SETUP.md) (`./scripts/setup.sh`, then `./scripts/start-n8n.sh`).

## Shipping to other people

Gmail's read scopes are restricted, so publishing the Gmail connector needs Google verification and an annual security assessment. IMAP avoids that. Details in [docs/SHIPPING.md](docs/SHIPPING.md).
