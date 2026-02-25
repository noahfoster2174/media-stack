#!/bin/bash
# download-movie.sh — Search and add a movie to Radarr for automatic download
# Usage: ./download-movie.sh "Movie Name"
# Called by macOS Shortcut or directly from Terminal

set -euo pipefail

# Load credentials from .env (fall back to defaults)
ENV_FILE="$(cd "$(dirname "$0")" && pwd)/.env"
if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    set -a; source "$ENV_FILE"; set +a
fi

RADARR_URL="http://localhost:7878"
RADARR_API_KEY="${RADARR_API_KEY:?Set RADARR_API_KEY in .env}"
QUALITY_PROFILE_ID=4  # HD-1080p

QBT_URL="http://localhost:8080"
QBT_USER="${QBT_USER:-admin}"
QBT_PASS="${QBT_PASS:?Set QBT_PASS in .env}"

# --- Status mode: show all active downloads ---
if [ "${1:-}" = "--status" ]; then
    # Login to qBittorrent
    QBT_COOKIE=$(curl -s -c - -X POST "$QBT_URL/api/v2/auth/login" \
        -d "username=$QBT_USER&password=$QBT_PASS" 2>/dev/null | grep SID | awk '{print $NF}')

    if [ -z "$QBT_COOKIE" ]; then
        echo "ERROR: Cannot connect to qBittorrent"
        exit 1
    fi

    curl -s -b "SID=$QBT_COOKIE" "$QBT_URL/api/v2/torrents/info" 2>/dev/null | python3 -c "
import sys, json
torrents = json.load(sys.stdin)
active = [t for t in torrents if t.get('progress', 1) < 1]
seeding = [t for t in torrents if t.get('progress', 0) >= 1]

if not active and not seeding:
    print('No active downloads.')
    sys.exit(0)

if active:
    for t in active:
        name = t.get('name', '?')
        pct = t.get('progress', 0) * 100
        size_gb = t.get('size', 0) / (1024**3)
        speed_mb = t.get('dlspeed', 0) / (1024**2)
        eta = t.get('eta', 0)
        if eta > 0 and eta < 8640000:
            mins, secs = divmod(eta, 60)
            hrs, mins = divmod(mins, 60)
            eta_str = f'{hrs}h{mins:02d}m' if hrs else f'{mins}m{secs:02d}s'
        else:
            eta_str = '??'
        bar_len = 20
        filled = int(bar_len * pct / 100)
        bar = '█' * filled + '░' * (bar_len - filled)
        print(f'{name}')
        print(f'  {bar} {pct:.1f}% of {size_gb:.1f} GB  |  {speed_mb:.1f} MB/s  |  ETA: {eta_str}')
        print()

if seeding:
    print(f'Seeding: {len(seeding)} torrent(s)')
"
    exit 0
fi

if [ $# -eq 0 ] || [ -z "$1" ]; then
    echo "ERROR: No movie name provided"
    echo "Usage: ./download-movie.sh \"Movie Name\""
    echo "       ./download-movie.sh --status"
    exit 1
fi

SEARCH_TERM="$1"

# Check if Radarr is reachable
if ! curl -s --max-time 3 "$RADARR_URL/api/v3/health" -H "X-Api-Key: $RADARR_API_KEY" > /dev/null 2>&1; then
    echo "ERROR: Radarr is not running. Start the stack with: cd ~/media-stack && docker compose up -d"
    exit 1
fi

# Search for the movie
RESULTS=$(curl -s "$RADARR_URL/api/v3/movie/lookup?term=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$SEARCH_TERM'))")" \
    -H "X-Api-Key: $RADARR_API_KEY")

# Parse top 5 results
PARSED=$(echo "$RESULTS" | python3 -c "
import sys, json
movies = json.load(sys.stdin)[:5]
if not movies:
    print('NO_RESULTS')
    sys.exit(0)
for i, m in enumerate(movies):
    tmdb = m.get('tmdbId', 0)
    title = m.get('title', 'Unknown')
    year = m.get('year', '?')
    print(f'{i+1}. {title} ({year}) [tmdb:{tmdb}]')
")

if [ "$PARSED" = "NO_RESULTS" ]; then
    echo "ERROR: No results found for '$SEARCH_TERM'"
    exit 1
fi

# If called with "search" as second arg, just print results and exit (for Shortcut)
if [ $# -ge 2 ] && [ "$2" = "search" ]; then
    echo "$PARSED"
    exit 0
fi

# If called with a number as second argument, use it; otherwise show results interactively
if [ $# -ge 2 ] && [ -n "$2" ]; then
    CHOICE="$2"
else
    echo "$PARSED"
    echo ""
    read -p "Pick a number (1-5), or 0 to cancel: " CHOICE
fi

if [ "$CHOICE" = "0" ]; then
    echo "Cancelled."
    exit 0
fi

# Get the selected movie's tmdbId and check if already in library
SELECTED=$(echo "$RESULTS" | python3 -c "
import sys, json
movies = json.load(sys.stdin)[:5]
idx = int('$CHOICE') - 1
if idx < 0 or idx >= len(movies):
    print('INVALID')
    sys.exit(0)
m = movies[idx]
print(json.dumps({
    'title': m['title'],
    'year': m.get('year'),
    'tmdbId': m['tmdbId'],
    'titleSlug': m.get('titleSlug', ''),
    'images': m.get('images', [])
}))
")

if [ "$SELECTED" = "INVALID" ]; then
    echo "ERROR: Invalid choice"
    exit 1
fi

TMDB_ID=$(echo "$SELECTED" | python3 -c "import sys,json; print(json.load(sys.stdin)['tmdbId'])")
TITLE=$(echo "$SELECTED" | python3 -c "import sys,json; m=json.load(sys.stdin); print(f\"{m['title']} ({m['year']})\")")

# Check if movie already exists in Radarr
EXISTS=$(curl -s "$RADARR_URL/api/v3/movie" -H "X-Api-Key: $RADARR_API_KEY" | python3 -c "
import sys, json
movies = json.load(sys.stdin)
tmdb = $TMDB_ID
for m in movies:
    if m.get('tmdbId') == tmdb:
        has_file = m.get('hasFile', False)
        if has_file:
            print('HAS_FILE')
        else:
            print('MONITORED')
        sys.exit(0)
print('NEW')
")

if [ "$EXISTS" = "HAS_FILE" ]; then
    echo "ALREADY_EXISTS: $TITLE is already in your library"
    exit 0
elif [ "$EXISTS" = "MONITORED" ]; then
    echo "ALREADY_MONITORED: $TITLE is already being monitored (downloading or searching)"
    exit 0
fi

# Add the movie to Radarr with search
ADD_PAYLOAD=$(echo "$SELECTED" | python3 -c "
import sys, json
m = json.load(sys.stdin)
add = {
    'title': m['title'],
    'year': m['year'],
    'tmdbId': m['tmdbId'],
    'titleSlug': m['titleSlug'],
    'images': m['images'],
    'qualityProfileId': $QUALITY_PROFILE_ID,
    'rootFolderPath': '/movies',
    'monitored': True,
    'minimumAvailability': 'released',
    'addOptions': {'searchForMovie': True}
}
print(json.dumps(add))
")

RESULT=$(curl -s -X POST "$RADARR_URL/api/v3/movie" \
    -H "X-Api-Key: $RADARR_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$ADD_PAYLOAD")

# Check if it worked
SUCCESS=$(echo "$RESULT" | python3 -c "
import sys, json
r = json.load(sys.stdin)
if r.get('id'):
    print(f\"ADDED: {r['title']} ({r.get('year', '?')})\")
elif isinstance(r, list):
    msgs = [e.get('errorMessage','') for e in r]
    print(f\"ERROR: {'; '.join(msgs)}\")
else:
    print(f\"ERROR: {r.get('message', 'Unknown error')}\")
")

echo "$SUCCESS"
