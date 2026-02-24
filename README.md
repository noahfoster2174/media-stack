# Media Stack

Automated home media pipeline using Docker.

## Services

| Service | Port | Purpose |
|---------|------|---------|
| qBittorrent | 8080 | Torrent client (Mullvad SOCKS5 proxy) |
| Prowlarr | 9696 | Indexer manager |
| Radarr | 7878 | Movie automation |
| Sonarr | 8989 | TV show automation |

Plex Media Server runs separately (not containerized).

## Quick start

```bash
# Copy .env and fill in Mullvad account number
cp .env.example .env

# Start all services
docker compose up -d

# Check status
docker compose ps

# View logs
docker compose logs -f

# Stop
docker compose down
```

## Directory layout

```
~/Downloads/
  torrents/    # Active downloads (qBittorrent)
  Movies/      # Completed movies (Radarr -> Plex)
  TV Shows/    # Completed TV (Sonarr -> Plex)
```

## Service UIs

- qBittorrent: http://localhost:8080
- Prowlarr: http://localhost:9696
- Radarr: http://localhost:7878
- Sonarr: http://localhost:8989
- Plex: http://localhost:32400/web

## Configuration

After first `docker compose up -d`:

1. **qBittorrent**: Change default password, configure Mullvad SOCKS5 proxy
2. **Prowlarr**: Add indexers (The Pirate Bay), connect to Radarr + Sonarr
3. **Radarr**: Set root folder `/movies`, add qBittorrent as download client
4. **Sonarr**: Set root folder `/tv`, add qBittorrent as download client
5. **Plex**: Verify library folders point to Movies and TV Shows directories
