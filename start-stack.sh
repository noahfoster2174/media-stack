#!/bin/bash
# Ensure the Reelz server (and its self-healing supervisor) is up — headless, no browser.
# Idempotent and safe to run repeatedly. Used by launch-app.sh and runnable on its own.
# Once the server is up, its supervisor auto-heals Docker / containers / Mullvad / Ollama.
set -u
PORT=9999
URL="http://localhost:$PORT"
AGENT="com.reelz.server"
PLIST="$HOME/Library/LaunchAgents/$AGENT.plist"
APP_DIR="$HOME/media-stack/app"
LOG="$HOME/Library/Logs/reelz.log"

reachable() { curl -s --max-time 2 "$URL" >/dev/null 2>&1; }

if reachable; then exit 0; fi

# Preferred: the LaunchAgent owns the server (KeepAlive restarts it on crash); just (re)start it.
if [ -f "$PLIST" ]; then
    launchctl kickstart -k "gui/$(id -u)/$AGENT" 2>/dev/null \
        || launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null
else
    # Fallback when the agent isn't installed: start a background server directly.
    ( cd "$APP_DIR" && exec python3 server.py ) >>"$LOG" 2>&1 &
fi

for i in $(seq 1 40); do
    reachable && exit 0
    sleep 0.25
done
exit 1
