#!/bin/bash
# Quick health check for the Reelz media stack, chat backend, and web app.
# A 200/401/403 all mean "the service is answering" — only a connection failure is DOWN.
set -u

check() {
    local name="$1" url="$2"
    printf "  %-13s " "$name"
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "$url" 2>/dev/null)
    if [ "$code" != "000" ] && [ -n "$code" ]; then
        echo "✓ up ($code)"
    else
        echo "✗ DOWN"
    fi
}

echo "Media stack:"
check "qBittorrent" http://localhost:8080/
check "Radarr"      http://localhost:7878/ping
check "Sonarr"      http://localhost:8989/ping
check "Prowlarr"    http://localhost:9696/ping
echo "Chat backend:"
check "Ollama"      http://localhost:11434/api/tags
echo "Web app:"
check "Reelz UI"    http://localhost:9999/
