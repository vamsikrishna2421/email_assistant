#!/usr/bin/env bash
#
# Starts the local n8n instance that hosts the Jerry multi-agent assistant.
#
#   ./scripts/start-n8n.sh
#
# Everything is self-contained under runtime/ — n8n itself, its SQLite database,
# and its encryption key. Nothing is written to ~/.n8n and your system Node
# (v26) is never used; n8n runs on the Homebrew-installed Node 22 LTS.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODE22_BIN="/usr/local/opt/node@22/bin"

if [ ! -x "$NODE22_BIN/node" ]; then
  echo "error: Node 22 not found at $NODE22_BIN" >&2
  echo "       run: brew install node@22" >&2
  exit 1
fi

if [ ! -d "$PROJECT_ROOT/runtime/node_modules/n8n" ]; then
  echo "error: n8n is not installed" >&2
  echo "       run: cd runtime && PATH=\"$NODE22_BIN:\$PATH\" npm install" >&2
  exit 1
fi

export PATH="$NODE22_BIN:$PATH"

# Keep all n8n state inside the project instead of ~/.n8n.
export N8N_USER_FOLDER="$PROJECT_ROOT/runtime"

# Load user secrets (Gemini key, Fireflies key) if present.
if [ -f "$PROJECT_ROOT/config/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/config/.env"
  set +a
fi

# The OAuth callback Google redirects to must match what n8n advertises, so pin
# the host/port explicitly rather than letting n8n guess from the request.
export N8N_HOST="${N8N_HOST:-localhost}"
export N8N_PORT="${N8N_PORT:-5678}"
export N8N_PROTOCOL="${N8N_PROTOCOL:-http}"
export N8N_WEBHOOK_URL="${N8N_WEBHOOK_URL:-http://localhost:5678/}"
export N8N_EDITOR_BASE_URL="${N8N_EDITOR_BASE_URL:-http://localhost:5678/}"

# Pin the settings whose defaults n8n has announced it will change, so a future
# `npm update` cannot silently alter this instance's behaviour.
export N8N_UNVERIFIED_PACKAGES_ENABLED=true
export N8N_RUNNERS_TASK_TIMEOUT=300
export N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS=true
export DB_SQLITE_POOL_SIZE=5
export N8N_DIAGNOSTICS_ENABLED="${N8N_DIAGNOSTICS_ENABLED:-false}"

echo "Jerry n8n  ->  http://localhost:${N8N_PORT}"
echo "state dir  ->  $N8N_USER_FOLDER/.n8n"
echo

exec "$PROJECT_ROOT/runtime/node_modules/.bin/n8n" start
