#!/bin/bash
# One-command Reelz setup for a fresh clone (macOS/Apple Silicon). Idempotent — safe to re-run.
# Installs the login LaunchAgents, pulls + pins the chat model, wires SwiftBar, and starts
# the server (whose supervisor then brings the rest of the stack up). It does NOT install the
# heavy prereqs (Docker/Ollama/Mullvad/Plex) — it checks for them and points you at the README.
set -u
ROOT="$HOME/media-stack"
AGENTS="$HOME/Library/LaunchAgents"
UID_N="$(id -u)"
PYTHON="$(command -v python3 || echo /opt/homebrew/bin/python3)"
say() { printf "\n\033[1m▸ %s\033[0m\n" "$1"; }

say "Checking prerequisites"
for t in docker ollama python3 curl; do
    if command -v "$t" >/dev/null 2>&1; then echo "  ✓ $t"; else echo "  ✗ $t missing — install it (see README)"; fi
done
command -v mullvad >/dev/null 2>&1 && echo "  ✓ mullvad" || echo "  ○ mullvad missing (optional, for private torrents)"

say "Config (.env)"
if [ -f "$ROOT/.env" ]; then
    echo "  ✓ .env already present"
else
    cp "$ROOT/.env.example" "$ROOT/.env"
    echo "  → created .env from template — FILL IN your keys before first use"
fi

if command -v ollama >/dev/null 2>&1; then
    say "Chat model (llama3.2:3b)"
    ollama pull llama3.2:3b
    brew services start ollama >/dev/null 2>&1   # ensure the server runs; supervisor keeps the model warm
    echo "  ✓ model pulled; Ollama running (supervisor re-pins keep_alive so it stays warm)"
fi

say "LaunchAgents (start at login)"
mkdir -p "$AGENTS" "$HOME/Library/Logs"
sed -e "s|__HOME__|$HOME|g" -e "s|__PYTHON__|$PYTHON|g" \
    "$ROOT/launchd/com.reelz.server.plist" > "$AGENTS/com.reelz.server.plist"
cp "$ROOT/launchd/com.reelz.ollama-warm.plist" "$AGENTS/com.reelz.ollama-warm.plist"
for a in com.reelz.server com.reelz.ollama-warm; do
    launchctl bootout "gui/$UID_N/$a" 2>/dev/null
    if launchctl bootstrap "gui/$UID_N" "$AGENTS/$a.plist" 2>/dev/null; then echo "  ✓ loaded $a"; else echo "  ✗ failed to load $a"; fi
done

say "Menubar (SwiftBar)"
chmod +x "$ROOT"/swiftbar/*.sh 2>/dev/null
if [ -d /Applications/SwiftBar.app ]; then
    defaults write com.ameba.SwiftBar PluginDirectory "$ROOT/swiftbar"
    open -a SwiftBar 2>/dev/null
    echo "  ✓ SwiftBar pointed at $ROOT/swiftbar"
else
    echo "  ○ not installed (optional): brew install --cask swiftbar, then re-run setup"
fi

chmod +x "$ROOT"/*.sh 2>/dev/null

say "Done"
echo "  The Reelz server is starting; its supervisor brings up Docker + the stack automatically."
echo "  • Fill in ~/media-stack/.env if you haven't yet."
echo "  • Open the app:  ~/media-stack/launch-app.sh"
echo "  • Check status:  make health   (or the 🎬 menubar item)"
