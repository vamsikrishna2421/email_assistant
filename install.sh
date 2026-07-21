#!/usr/bin/env bash
#
# One-click local install.
#
#   ./install.sh
#
# Builds Jerry, puts it in ~/Applications, and starts it at login. Everything
# else — the AI engine, the model download — Jerry handles itself on first run.

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$HOME/Applications/Jerry.app"
PLIST="$HOME/Library/LaunchAgents/com.jerry.inbox.plist"

echo "Installing Jerry…"

if [ ! -d "$ROOT/.venv" ]; then
  echo "  setting up build environment"
  /usr/local/opt/python@3.12/bin/python3.12 -m venv "$ROOT/.venv" 2>/dev/null \
    || python3.12 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install -q -e "$ROOT" pyinstaller
fi

echo "  building"
(cd "$ROOT/packaging" && "$ROOT/.venv/bin/pyinstaller" --noconfirm --clean jerry.spec >/dev/null)

echo "  installing to ~/Applications"
mkdir -p "$HOME/Applications"
# Quit any running copy so the bundle can be replaced cleanly.
pkill -f "Jerry.app/Contents/MacOS/Jerry" 2>/dev/null || true
launchctl bootout "gui/$(id -u)/com.jerry.inbox" 2>/dev/null || true
sleep 1
rm -rf "$APP"
cp -R "$ROOT/packaging/dist/Jerry.app" "$APP"

echo "  starting at login"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.jerry.inbox</string>
    <key>ProgramArguments</key>
    <array><string>$APP/Contents/MacOS/Jerry</string></array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>30</integer>
    <key>StandardOutPath</key><string>$HOME/Library/Application Support/Jerry/logs/app.log</string>
    <key>StandardErrorPath</key><string>$HOME/Library/Application Support/Jerry/logs/app.error.log</string>
</dict>
</plist>
PLIST_EOF

mkdir -p "$HOME/Library/Application Support/Jerry/logs"
launchctl bootstrap "gui/$(id -u)" "$PLIST"

printf "  waiting for Jerry"
for _ in $(seq 1 30); do
  if curl -s -o /dev/null http://127.0.0.1:8765/ 2>/dev/null; then
    echo " — running"
    echo
    echo "Jerry is in your menu bar. Open it at http://127.0.0.1:8765"
    exit 0
  fi
  printf "."; sleep 2
done
echo
echo "Jerry did not answer in time. Check ~/Library/Application Support/Jerry/logs/app.error.log" >&2
exit 1
