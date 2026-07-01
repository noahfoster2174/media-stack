#!/bin/bash
# Launch the Reelz app: make sure the self-healing stack/server is up, then open the window.
# All the bring-up + healing lives in start-stack.sh (+ the server's supervisor), so this stays tiny.
PORT=9999
URL="http://localhost:$PORT"

"$HOME/media-stack/start-stack.sh"

open -na "Google Chrome" --args --app="$URL" --window-size=900,760
