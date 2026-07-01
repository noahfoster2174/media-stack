#!/bin/bash
# SwiftBar plugin — Reelz menubar status (refreshes every 5s per the ".5s." in the name).
# Renders /api/health as a colored icon + a per-service menu with one-click Fix actions.
# Absolute paths for curl/python because SwiftBar runs plugins with a minimal environment.
CURL=/usr/bin/curl
PY=/opt/homebrew/bin/python3
[ -x "$PY" ] || PY=/usr/bin/python3

HEALTH=$("$CURL" -s --max-time 3 http://localhost:9999/api/health)
if [ -z "$HEALTH" ]; then
    echo "🎬"
    echo "---"
    echo "Reelz server not running | color=red"
    echo "Start Reelz | bash=$HOME/media-stack/start-stack.sh terminal=false"
    exit 0
fi

echo "$HEALTH" | "$PY" -c '
import sys, json, os
HOME = os.path.expanduser("~")
HEAL = HOME + "/media-stack/swiftbar/reelz-heal.sh"
LAUNCH = HOME + "/media-stack/launch-app.sh"
d = json.load(sys.stdin)
ic = {"up": "\U0001F7E2", "degraded": "\U0001F7E1", "down": "\U0001F534"}.get(d["overall"], "⚪")
print("\U0001F3AC " + ic)                      # menubar title: clapperboard + status dot
print("---")
print("Reelz — " + d["overall"])
print("---")
for s in d["services"]:
    dot = {"up": "\U0001F7E2", "starting": "\U0001F7E1", "down": "\U0001F534"}.get(s["status"], "⚪")
    print(dot + " " + s["label"])
    if s["status"] != "up" and s.get("can_heal"):
        print("-- Fix " + s["label"] + " | bash=" + HEAL + " param1=" + s["name"] + " terminal=false refresh=true")
print("---")
print("Open Reelz | bash=" + LAUNCH + " terminal=false")
print("Refresh now | refresh=true")
'
