#!/bin/bash
# Helper invoked by the SwiftBar menu's "Fix" items — triggers a supervisor heal.
/usr/bin/curl -s -X POST http://localhost:9999/api/heal \
    -H "Content-Type: application/json" -d "{\"service\":\"$1\"}" >/dev/null 2>&1
