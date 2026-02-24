#!/bin/bash
# Launch the Download Movie web app — reuse running server or start fresh
PORT=9999
PID_FILE="/tmp/download-movie-server.pid"
APP_DIR="$HOME/media-stack/app"

# Reuse running server
if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    open -na "Google Chrome" --args --app="http://localhost:$PORT"
    exit 0
fi

# Start new server
cd "$APP_DIR"
python3 server.py &
echo $! > "$PID_FILE"

# Wait for server ready (up to 5s)
for i in {1..20}; do
    curl -s "http://localhost:$PORT" > /dev/null 2>&1 && break
    sleep 0.25
done

open -na "Google Chrome" --args --app="http://localhost:$PORT"
