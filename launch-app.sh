#!/bin/bash
# Launch the Download Movie web app — reuse running server or start fresh
PORT=9999
PID_FILE="/tmp/download-movie-server.pid"
APP_DIR="$HOME/media-stack/app"
URL="http://localhost:$PORT"

open_app() {
    open -na "Google Chrome" --args --app="$URL" --window-size=800,700
}

# Check if server is already running and responsive
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    if curl -s --max-time 2 "$URL" > /dev/null 2>&1; then
        open_app
        exit 0
    fi
    # PID alive but server not responding — kill stale process
    kill "$(cat "$PID_FILE")" 2>/dev/null
    rm -f "$PID_FILE"
fi

# Start new server
cd "$APP_DIR"
python3 server.py &
echo $! > "$PID_FILE"

# Wait for server ready (up to 5s)
for i in {1..20}; do
    curl -s "$URL" > /dev/null 2>&1 && break
    sleep 0.25
done

open_app
