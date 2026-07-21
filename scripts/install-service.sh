#!/usr/bin/env bash
#
# Registers n8n as a macOS LaunchAgent so it starts at login and restarts if it
# crashes.
#
#   ./scripts/install-service.sh          install and start
#   ./scripts/install-service.sh remove   stop and unregister
#
# Why this matters: the digest runs on a schedule, and a schedule only fires
# while n8n is running. Started by hand from a terminal, n8n dies when you close
# the window, log out, or reboot — and a digest that silently stops arriving
# looks exactly like a quiet inbox. Ollama is already a Homebrew service, so
# this is the missing half.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.jerry.n8n"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "${1:-install}" = "remove" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "removed $LABEL — n8n will no longer start at login"
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT_ROOT/runtime/logs"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$PROJECT_ROOT/scripts/start-n8n.sh</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$PROJECT_ROOT</string>

    <key>RunAtLoad</key>
    <true/>

    <!-- Restart if it exits for any reason. A scheduled digest is only as
         reliable as the process that owns the timer. -->
    <key>KeepAlive</key>
    <true/>

    <!-- Do not hammer the machine if it is crash-looping on a bad config. -->
    <key>ThrottleInterval</key>
    <integer>30</integer>

    <key>StandardOutPath</key>
    <string>$PROJECT_ROOT/runtime/logs/n8n.log</string>
    <key>StandardErrorPath</key>
    <string>$PROJECT_ROOT/runtime/logs/n8n.error.log</string>
</dict>
</plist>
PLIST_EOF

# Stop anything already running so the agent owns the port.
pkill -f "runtime/node_modules/.bin/n8n" 2>/dev/null || true
sleep 3

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "installed $LABEL"
echo "  n8n now starts at login and restarts if it dies"
echo "  logs: runtime/logs/n8n.log"
echo
printf 'waiting for n8n'
for _ in $(seq 1 30); do
  if curl -s -o /dev/null http://localhost:5678/; then echo " — up at http://localhost:5678"; exit 0; fi
  printf '.'; sleep 2
done
echo
echo "warning: n8n did not answer within 60s — check runtime/logs/n8n.error.log" >&2
exit 1
