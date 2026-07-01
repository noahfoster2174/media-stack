# Media Stack

Dockerized home media automation pipeline on macOS.

## Architecture

```
Prowlarr (indexer) --> Radarr (movies) --> qBittorrent --> ~/Downloads/Movies --> Plex
                  --> Sonarr (TV)      --> qBittorrent --> ~/Downloads/TV Shows --> Plex
```

qBittorrent routes all traffic through Mullvad SOCKS5 proxy for privacy.

## Services

All services use LinuxServer.io images with PUID=501/PGID=20 (macOS default user).

| Service | Container name | Port | Image |
|---------|---------------|------|-------|
| qBittorrent | qbittorrent | 8080 | lscr.io/linuxserver/qbittorrent |
| Prowlarr | prowlarr | 9696 | lscr.io/linuxserver/prowlarr |
| Radarr | radarr | 7878 | lscr.io/linuxserver/radarr |
| Sonarr | sonarr | 8989 | lscr.io/linuxserver/sonarr |

## Key paths

- Project: ~/media-stack/
- Config (gitignored): ~/media-stack/config/
- Torrent downloads: ~/Downloads/torrents
- Movies library: ~/Downloads/Movies
- TV library: ~/Downloads/TV Shows

## Container volume mappings

| Host path | Container path | Used by |
|-----------|---------------|---------|
| ~/Downloads/torrents | /downloads | qBittorrent, Radarr, Sonarr |
| ~/Downloads/Movies | /movies | qBittorrent, Radarr |
| ~/Downloads/TV Shows | /tv | qBittorrent, Sonarr |

## Environment

Secrets in `.env` (gitignored): PUID, PGID, TZ, MULLVAD_ACCOUNT, RADARR_API_KEY, QBT_USER, QBT_PASS.

## Common commands

```bash
docker compose up -d        # Start all services
docker compose down          # Stop all services
docker compose ps            # Status
docker compose logs -f       # Follow all logs
docker compose logs radarr   # Single service logs
docker compose pull          # Update images
docker compose up -d         # Recreate with new images
```

## Inter-service communication

Services reference each other by container name over Docker's internal network:
- Prowlarr -> Radarr: http://radarr:7878
- Prowlarr -> Sonarr: http://sonarr:8989
- Radarr/Sonarr -> qBittorrent: qbittorrent:8080

## VPN / Proxy

qBittorrent uses Mullvad SOCKS5 proxy (not a full VPN tunnel):
- Host: socks5.mullvad.net
- Port: 1080
- Username: Mullvad 16-digit account number
- Password: mullvad

## Download Movie Web App

Browser-based UI for searching and downloading movies through Radarr/qBittorrent.

### Architecture

```
launch-app.sh → starts Docker + Mullvad → starts Python proxy (port 9999) → opens Chrome --app mode
                                              ↓
                                     app/server.py (Flask-like proxy)
                                     ├── injects Radarr API key server-side
                                     ├── injects qBittorrent SID auth server-side
                                     └── serves app/index.html
```

### Files

| File | Purpose |
|------|---------|
| `app/server.py` | Python proxy server — handles auth injection, serves frontend |
| `app/index.html` | Single-file HTML/CSS/JS frontend |
| `launch-app.sh` | Starts Docker/Mullvad, launches proxy, opens Chrome in app mode |
| `download-movie.sh` | Standalone CLI tool (still works independently) |

### How it works

The proxy server (`server.py`) sits between the browser and the Radarr/qBittorrent APIs. It reads credentials from `.env` and injects them into API requests server-side, so the frontend never handles secrets directly.

### Features

- Search movies via Radarr API
- Pick releases and send to qBittorrent
- Auto-refreshing download progress
- Remove torrents from qBittorrent
- Auto-starts Docker services and Mullvad VPN on launch
- AI chat powered by a local Ollama model (no API costs, no cloud, runs on-device)
- Chat knows movie library and watch history via system prompt
- Markdown rendering in chat responses

### Chat

Chat runs against a **local Ollama** model over its OpenAI-compatible API — no Anthropic
API key or credits needed (the Max plan does not fund the developer API).

- `server.py` calls Ollama at `OLLAMA_URL` (default `http://localhost:11434/v1`) and
  translates its OpenAI-style stream into the Anthropic-style SSE events the frontend
  already parses (`content_block_delta`/`text_delta`, then `message_stop`), so
  `index.html` is unchanged.
- Model pinned via `OLLAMA_MODEL` in `.env` (currently `llama3.2:3b`). If blank,
  server.py auto-picks the first non-embedding model from `/v1/models`.
- **Always-warm:** the OpenAI `/v1/chat/completions` endpoint can't pass `keep_alive`, so each chat
  resets the model to Ollama's 5-min default and it would unload. Rather than fight Ollama's
  brew-managed plist (brew regenerates it), the **supervisor keeps it warm**: `keep_model_warm()`
  re-pins `keep_alive=-1` every ~4 min, and `_heal_ollama()` restarts the Ollama server if it's fully
  down. The `com.reelz.ollama-warm` LaunchAgent also preloads the model at login so the first chat
  after a reboot is instant. Verify with `ollama ps` (a loaded model = warm).
- Connection: close header ensures clean stream termination.
- Chat history lives in JS memory (not persisted across reloads).

**Prerequisites for Chat:** Ollama running as a service (`brew services start ollama`)
with the model pulled (`ollama pull llama3.2:3b`). If neither, Chat returns a clear
error; the rest of the app is unaffected.

### Credentials

All secrets read from `.env` (gitignored):
- `OLLAMA_URL` / `OLLAMA_MODEL` — local Ollama endpoint + model (for chat)
- `RADARR_API_KEY` — Radarr API key
- `QBT_USER` / `QBT_PASS` — qBittorrent login
- `MULLVAD_ACCOUNT` — Mullvad account number (for VPN)
- `ANTHROPIC_API_KEY` — legacy/unused (chat moved to local Ollama)

## Self-healing supervisor

`app/supervisor.py` is the brain that makes Reelz a hands-off daily driver. A background
thread probes every dependency (~5s) and **auto-heals** what's down, with a 45s cooldown so
it recovers transient failures without thrashing.

- **State:** each service is `up | starting | down`; overall is `up | degraded | down`
  (a *critical* service down → `down`; critical = Docker, qBittorrent, Radarr).
- **API:** `GET /api/health` (cached state), `POST /api/heal {service}` (manual fix).
- **Heals:** Docker off → `open -a Docker` + `compose up`; container unhealthy → `compose
  restart`; Mullvad down → `mullvad connect`; Ollama → warm/pin the model.
- **UI:** header **status pill** (green/amber/red) polling `/api/health`, a click-panel with
  per-service **Fix** buttons, and a **preflight overlay** that holds the app until core is
  green — so it never opens broken and never dead-ends on a 502.
- **Notifications:** the loop watches qBittorrent; a newly-finished download fires a macOS
  notification ("🍿 … is ready to watch"). `_new_completions()` is the pure, unit-tested core.

### Always-on (start at login)

- **`~/Library/LaunchAgents/com.reelz.server.plist`** — runs `server.py` with `KeepAlive`
  (restarts on crash) + `RunAtLoad` (starts at login). PATH is set explicitly so the
  supervisor's `docker`/`mullvad`/`ollama` shell-outs resolve headlessly. Logs to
  `~/Library/Logs/reelz.log`. Restart it with
  `launchctl kickstart -k gui/$(id -u)/com.reelz.server`.
- **`com.reelz.ollama-warm.plist`** — preloads the chat model at login.
- **`start-stack.sh`** — idempotent "ensure server reachable" (used by `launch-app.sh`, which
  now just calls it then opens Chrome). Reachability (curl :9999), not PID files, is the truth.

### Menubar (SwiftBar)

`swiftbar/reelz.5s.sh` renders `/api/health` as a 🎬 + colored dot in the menubar with a
per-service menu and Fix actions (`swiftbar/reelz-heal.sh`). SwiftBar's plugin dir is set via
`defaults write com.ameba.SwiftBar PluginDirectory ~/media-stack/swiftbar`.

### Phone access (opt-in)

Set `BIND_LAN=1` in `.env` and restart the server → it binds `0.0.0.0` (default is localhost).
Reach it at `http://<mac-lan-ip>:9999` from a phone on the same WiFi; the UI is responsive
(<640px). If Mullvad's full tunnel is up, LAN access also needs `mullvad lan set allow`.
No auth — home LAN only (a shared token is a possible later add).

### Troubleshooting

If the app loads but Explore/Search/Downloads fail silently while Chat still works → Docker Desktop is probably not running. Radarr (7878) and qBittorrent (8080) are down, but TMDB works independently.

If Chat fails while everything else works → Ollama isn't serving. Run `brew services start ollama` and `ollama pull llama3.2:3b` (verify with `curl -s localhost:11434/v1/models`; check the loaded model with `ollama ps`).

**Fix:** `open -a Docker && cd ~/media-stack && docker compose up -d` or just run `./launch-app.sh`.

**Verify:**
```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:7878/api/v3/health  # 200 or 401 = OK
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/api/v2/app/version  # 200 = OK
```

### Known improvements (backlog)

1. **Add timeouts to `urlopen` in server.py** — proxy requests to Radarr/qBittorrent have no timeout; hangs if service is partially up
2. **Use `fetchWithTimeout` consistently in index.html** — currently only used by Search; Explore and health check use raw `fetch()`
3. **Make health banner more prominent** — currently a small dismissible bar; could be a full-screen overlay when core services are down

### Launch methods

| Method | How |
|--------|-----|
| Spotlight | Type "Download Movie" → opens AppleScript .app at `~/Applications/Download Movie.app` |
| Keyboard shortcut | Ctrl+Cmd+M via Quick Action at `~/Library/Services/Download Movie.workflow/` |
| CLI | `./launch-app.sh` from `~/media-stack/` |
| CLI (no UI) | `./download-movie.sh` for terminal-only workflow |
