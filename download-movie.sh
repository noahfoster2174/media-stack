#!/bin/bash
# download-movie.sh — Search and add a movie to Radarr for automatic download
# Usage: ./download-movie.sh "Movie Name"
# Called by macOS Shortcut or directly from Terminal

set -euo pipefail

RADARR_URL="http://localhost:7878"
RADARR_API_KEY="REDACTED_API_KEY"
QUALITY_PROFILE_ID=4  # HD-1080p

if [ $# -eq 0 ] || [ -z "$1" ]; then
    echo "ERROR: No movie name provided"
    echo "Usage: ./download-movie.sh \"Movie Name\""
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
