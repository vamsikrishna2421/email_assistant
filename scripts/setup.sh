#!/usr/bin/env bash
#
# One-shot setup for Jerry. Safe to re-run — it overwrites the workflow and the
# credentials with whatever is currently in config/.env.
#
#   ./scripts/setup.sh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODE22="/usr/local/opt/node@22/bin/node"
cd "$PROJECT_ROOT"

if [ ! -x "$NODE22" ]; then
  echo "error: Node 22 missing — run: brew install node@22" >&2
  exit 1
fi

if [ ! -d runtime/node_modules/n8n ]; then
  echo "==> installing n8n (this takes a few minutes)"
  (cd runtime && PATH="/usr/local/opt/node@22/bin:$PATH" npm install)
fi

if pgrep -f "runtime/node_modules/.bin/n8n" >/dev/null 2>&1; then
  echo "error: n8n is running. Stop it first — the CLI writes to the same database." >&2
  exit 1
fi

echo "==> building workflow"
"$NODE22" scripts/build-workflow.mjs

echo "==> validating against installed n8n"
"$NODE22" scripts/validate-workflow.mjs

echo "==> importing workflow"
N8N_USER_FOLDER="$PROJECT_ROOT/runtime" \
  PATH="/usr/local/opt/node@22/bin:$PATH" \
  ./runtime/node_modules/.bin/n8n import:workflow --input=workflows/jerry.workflow.json

if [ -f config/.env ]; then
  echo "==> importing credentials"
  "$NODE22" scripts/import-credentials.mjs
else
  echo
  echo "config/.env not found — skipping credentials."
  echo "  cp config/.env.example config/.env   then re-run this script"
fi

cat <<'EOF'

Done. Start Jerry with:

  ./scripts/start-n8n.sh

then open http://localhost:5678 and pick "Jerry - Multi Agent Assistant System".
EOF
