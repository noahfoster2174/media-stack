# Media Stack — Reelz

A personal, **local-only** (no cloud) movie/TV downloader: a Dockerized *arr pipeline
plus **Reelz**, a single-file web app for searching, downloading, tracking your library,
and chatting with a **local AI model** that knows what you own.

```
Prowlarr ──▶ Radarr / Sonarr ──▶ qBittorrent (Mullvad SOCKS5) ──▶ ~/Downloads ──▶ Plex
                          ▲
              Reelz web app (app/) ── local Ollama chat
```

## Services

| Service | Port | Purpose |
|---------|------|---------|
| qBittorrent | 8080 | Torrent client (via Mullvad SOCKS5 proxy) |
| Prowlarr | 9696 | Indexer manager |
| Radarr | 7878 | Movie automation |
| Sonarr | 8989 | TV automation |
| Reelz web app | 9999 | Search / download / library / AI chat UI |
| Ollama | 11434 | Local LLM backend for the Chat tab |

Plex runs separately (not containerized).

## Quick start

```bash
cp .env.example .env          # then fill in the blanks (Mullvad account, API keys…)
make up                       # start the *arr stack (docker compose up -d)
ollama pull qwen2.5:3b       # one-time: pull the local chat model
make app                      # launch the Reelz web app (Chrome app window)
make health                   # verify everything is answering
```

`make help` lists all tasks (`up`, `down`, `restart`, `logs`, `ps`, `pull`, `health`, `app`).

## Container runtime

Runs on **Docker Desktop**. ⚠️ **Do not switch to OrbStack** here: OrbStack's networking
is blocked by the Mullvad full-tunnel that qBittorrent depends on (its SOCKS5 proxy at
`10.64.0.1` only exists inside the tunnel), so the host can't reach the containers. Docker
Desktop's vpnkit tolerates the VPN; OrbStack does not. Images are **pinned** in
`docker-compose.yml`; update deliberately by bumping a tag and running `make pull`.

## Directory layout

```
~/Downloads/
  torrents/    # active downloads (qBittorrent)
  Movies/      # completed movies (Radarr → Plex)
  TV Shows/    # completed TV (Sonarr → Plex)
~/media-stack/
  config/      # each service's persisted state (gitignored)
  app/         # Reelz web app (server.py + index.html)
```

## Service UIs

qBittorrent :8080 · Prowlarr :9696 · Radarr :7878 · Sonarr :8989 · Plex :32400/web

## First-run configuration

After the first `make up`:

1. **qBittorrent** — change the default password, set the Mullvad SOCKS5 proxy.
2. **Prowlarr** — add indexers, connect to Radarr + Sonarr.
3. **Radarr** — root folder `/movies`, add qBittorrent as the download client.
4. **Sonarr** — root folder `/tv`, add qBittorrent as the download client.
5. **Plex** — point libraries at the Movies and TV Shows folders.

## Privacy / kill-switch

qBittorrent routes torrents through a Mullvad SOCKS5 proxy. It is **fail-closed**: if the
proxy is unreachable, torrents simply don't connect (no clearnet fallback). If downloads
stall, check the proxy: `curl -x socks5://<account>:mullvad@socks5.mullvad.net:1080 https://api.ipify.org`.

## Self-healing & daily use

Reelz runs itself. A **supervisor** (`app/server.py` + `app/supervisor.py`) keeps the whole
stack healthy: it starts at login (`com.reelz.server` LaunchAgent, KeepAlive), auto-recovers
failures (Docker off, Mullvad dropped, model unloaded…), shows a live **status pill** in the
header (with one-click fixes), and holds a **preflight** screen until things are green — so it
never opens broken. A finished download pops a **notification**, and the **SwiftBar** menubar
item (🎬) lets you check/fix status without opening the app. Nothing to babysit.

**Phone (opt-in):** set `BIND_LAN=1` in `.env`, restart, and open `http://<mac-lan-ip>:9999`
from a phone on the same WiFi (needs `mullvad lan set allow` if the VPN is on).

## Architecture & internals

See [`CLAUDE.md`](CLAUDE.md) for the full architecture: container wiring, the Reelz proxy
server, the local-Ollama chat (with the always-warm setup), and troubleshooting.
