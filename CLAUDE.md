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

### Credentials

All secrets read from `.env` (gitignored):
- `RADARR_API_KEY` — Radarr API key
- `QBT_USER` / `QBT_PASS` — qBittorrent login
- `MULLVAD_ACCOUNT` — Mullvad account number (for VPN)

### Launch methods

| Method | How |
|--------|-----|
| Spotlight | Type "Download Movie" → opens AppleScript .app at `~/Applications/Download Movie.app` |
| Keyboard shortcut | Ctrl+Cmd+M via Quick Action at `~/Library/Services/Download Movie.workflow/` |
| CLI | `./launch-app.sh` from `~/media-stack/` |
| CLI (no UI) | `./download-movie.sh` for terminal-only workflow |
