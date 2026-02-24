#!/usr/bin/env python3
"""Proxy server for Reelz web app. Stdlib only, no pip installs."""

import datetime
import http.server
import json
import os
import random
import re
import sqlite3
import subprocess
import concurrent.futures
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env():
    """Read key=value pairs from .env file."""
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


_env = load_env()

PORT = 9999
RADARR_URL = "http://localhost:7878"
RADARR_API_KEY = _env.get("RADARR_API_KEY", "REDACTED_API_KEY")
QBT_URL = "http://localhost:8080"
QBT_USER = _env.get("QBT_USER", "admin")
QBT_PASS = _env.get("QBT_PASS", "REDACTED_PASSWORD")
COMPOSE_DIR = ENV_FILE.parent
NOTION_TOKEN = _env.get("NOTION_TOKEN", "")
NOTION_DATABASE_ID = _env.get("NOTION_DATABASE_ID", "")
TMDB_API_KEY = _env.get("TMDB_API_KEY", "")
TMDB_BASE = "https://api.themoviedb.org/3"
PLEX_URL = _env.get("PLEX_URL", "http://localhost:32400")
PLEX_TOKEN = _env.get("PLEX_TOKEN", "")
ANTHROPIC_API_KEY = _env.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = "claude-sonnet-4-6"
MOVIES_DIR = Path.home() / "Downloads" / "Movies"
DB_PATH = Path(__file__).resolve().parent / "data.db"

qbt_sid = None


# ---- SQLite ----

def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watched (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tmdb_id INTEGER NOT NULL UNIQUE,
            title TEXT NOT NULL,
            year INTEGER,
            watched_at TEXT NOT NULL DEFAULT (datetime('now')),
            rating INTEGER CHECK(rating BETWEEN 1 AND 5),
            review_text TEXT DEFAULT '',
            poster_url TEXT DEFAULT ''
        )
    """)
    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


init_db()


def ensure_services():
    """Start Docker, Mullvad, and docker compose if not already running."""
    results = {}

    # 1. Mullvad VPN
    try:
        status = subprocess.run(
            ["mullvad", "status"], capture_output=True, text=True, timeout=5
        )
        if "Connected" not in status.stdout:
            subprocess.run(["mullvad", "connect"], timeout=10)
            results["mullvad"] = "connecting"
        else:
            results["mullvad"] = "already connected"
    except Exception as e:
        results["mullvad"] = f"error: {e}"

    # 2. Docker Desktop
    try:
        info = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=5
        )
        if info.returncode != 0:
            subprocess.run(["open", "-a", "Docker"], timeout=5)
            results["docker"] = "starting"
        else:
            results["docker"] = "already running"
    except Exception as e:
        results["docker"] = f"error: {e}"

    # 3. Docker Compose stack
    try:
        ps = subprocess.run(
            ["docker", "compose", "ps", "--format", "{{.State}}"],
            capture_output=True, text=True, timeout=10, cwd=str(COMPOSE_DIR)
        )
        states = [s.strip() for s in ps.stdout.strip().splitlines() if s.strip()]
        if not states or any(s != "running" for s in states):
            subprocess.Popen(
                ["docker", "compose", "up", "-d"],
                cwd=str(COMPOSE_DIR),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            results["compose"] = "starting"
        else:
            results["compose"] = "already running"
    except Exception as e:
        results["compose"] = f"error: {e}"

    return results


def qbt_login():
    """Authenticate with qBittorrent, store SID cookie."""
    global qbt_sid
    data = urllib.parse.urlencode({"username": QBT_USER, "password": QBT_PASS}).encode()
    try:
        resp = urllib.request.urlopen(urllib.request.Request(f"{QBT_URL}/api/v2/auth/login", data=data))
        for key, val in resp.getheaders():
            if key.lower() == "set-cookie" and "SID=" in val:
                qbt_sid = val.split("SID=")[1].split(";")[0]
                return True
    except Exception:
        pass
    return False


class Handler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path in ("/", ""):
            self._serve_index()
        elif self.path == "/api/watched":
            self._get_watched()
        elif self.path == "/api/tmdb/recommendations":
            self._tmdb_recommendations()
        elif self.path.startswith("/api/tmdb/provider/"):
            self._tmdb_provider()
        elif self.path.startswith("/api/tmdb/"):
            self._proxy_tmdb()
        elif self.path.startswith("/api/radarr/"):
            self._proxy_radarr()
        elif self.path.startswith("/api/qbt/"):
            self._proxy_qbt()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/ensure-services":
            self._ensure_services()
        elif self.path == "/api/watched":
            self._post_watched()
        elif self.path == "/api/notion/review":
            self._post_notion_review()
        elif self.path == "/api/import-library":
            self._import_library()
        elif self.path == "/api/plex/sync-watched":
            self._plex_sync_watched()
        elif self.path == "/api/plex/scan-library":
            self._plex_scan_library()
        elif self.path == "/api/chat":
            self._chat()
        elif self.path.startswith("/api/radarr/"):
            self._proxy_radarr()
        elif self.path.startswith("/api/qbt/"):
            self._proxy_qbt_post()
        else:
            self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("/api/watched/"):
            self._delete_watched()
        elif self.path.startswith("/api/radarr/"):
            self._proxy_radarr()
        else:
            self.send_error(404)

    def _ensure_services(self):
        results = ensure_services()
        data = json.dumps(results).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_index(self):
        p = Path(__file__).parent / "index.html"
        if not p.exists():
            return self.send_error(404, "index.html not found")
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _proxy_radarr(self):
        api_path = self.path[len("/api/radarr/"):]
        url = f"{RADARR_URL}/api/v3/{api_path}"
        headers = {"X-Api-Key": RADARR_API_KEY}
        body = None
        if self.command == "POST":
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n) if n else None
            headers["Content-Type"] = "application/json"
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method=self.command)
            resp = urllib.request.urlopen(req)
            self._relay(resp.status, resp.read(), resp.getheader("Content-Type", "application/json"))
        except urllib.error.HTTPError as e:
            self._relay(e.code, e.read(), "application/json")
        except urllib.error.URLError:
            self.send_error(502, "Radarr unreachable — is Docker running?")

    def _proxy_qbt(self):
        global qbt_sid
        api_path = self.path[len("/api/qbt/"):]
        url = f"{QBT_URL}/api/v2/{api_path}"
        if not qbt_sid and not qbt_login():
            return self.send_error(502, "qBittorrent auth failed")
        for attempt in range(2):
            try:
                req = urllib.request.Request(url)
                req.add_header("Cookie", f"SID={qbt_sid}")
                resp = urllib.request.urlopen(req)
                return self._relay(200, resp.read(), resp.getheader("Content-Type", "application/json"))
            except urllib.error.HTTPError as e:
                if e.code == 403 and attempt == 0:
                    qbt_sid = None
                    if qbt_login():
                        continue
                    return self.send_error(502, "qBittorrent re-auth failed")
                return self._relay(e.code, e.read(), "application/json")
            except urllib.error.URLError:
                return self.send_error(502, "qBittorrent unreachable — is Docker running?")

    def _proxy_qbt_post(self):
        """Proxy POST to qBittorrent (e.g. torrents/delete)."""
        global qbt_sid
        api_path = self.path[len("/api/qbt/"):]
        url = f"{QBT_URL}/api/v2/{api_path}"
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n) if n else None
        if not qbt_sid and not qbt_login():
            return self.send_error(502, "qBittorrent auth failed")
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, data=body, method="POST")
                req.add_header("Cookie", f"SID={qbt_sid}")
                if body:
                    req.add_header("Content-Type", self.headers.get("Content-Type", "application/x-www-form-urlencoded"))
                resp = urllib.request.urlopen(req)
                return self._relay(200, resp.read(), resp.getheader("Content-Type", "text/plain"))
            except urllib.error.HTTPError as e:
                if e.code == 403 and attempt == 0:
                    qbt_sid = None
                    if qbt_login():
                        continue
                    return self.send_error(502, "qBittorrent re-auth failed")
                return self._relay(e.code, e.read(), "text/plain")
            except urllib.error.URLError:
                return self.send_error(502, "qBittorrent unreachable")

    # ---- Watched CRUD ----

    def _get_watched(self):
        conn = get_db()
        rows = conn.execute("SELECT * FROM watched ORDER BY watched_at DESC").fetchall()
        conn.close()
        data = json.dumps([dict(r) for r in rows]).encode()
        self._relay(200, data, "application/json")

    def _post_watched(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n)) if n else {}
        tmdb_id = body.get("tmdb_id")
        title = body.get("title", "")
        if not tmdb_id or not title:
            return self._relay(400, json.dumps({"error": "tmdb_id and title required"}).encode(), "application/json")
        conn = get_db()
        conn.execute("""
            INSERT INTO watched (tmdb_id, title, year, rating, review_text, poster_url)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(tmdb_id) DO UPDATE SET
                rating = excluded.rating,
                review_text = excluded.review_text,
                watched_at = datetime('now'),
                poster_url = excluded.poster_url
        """, (tmdb_id, title, body.get("year"), body.get("rating"), body.get("review_text", ""), body.get("poster_url", "")))
        conn.commit()
        row = conn.execute("SELECT * FROM watched WHERE tmdb_id = ?", (tmdb_id,)).fetchone()
        conn.close()
        self._relay(200, json.dumps(dict(row)).encode(), "application/json")

    def _delete_watched(self):
        watched_id = self.path.split("/")[-1]
        conn = get_db()
        conn.execute("DELETE FROM watched WHERE id = ?", (watched_id,))
        conn.commit()
        conn.close()
        self._relay(200, json.dumps({"ok": True}).encode(), "application/json")

    # ---- Notion push ----

    def _post_notion_review(self):
        if not NOTION_TOKEN or not NOTION_DATABASE_ID:
            return self._relay(400, json.dumps({
                "error": "Add NOTION_TOKEN and NOTION_DATABASE_ID to .env"
            }).encode(), "application/json")
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n)) if n else {}
        notion_body = json.dumps({
            "parent": {"database_id": NOTION_DATABASE_ID},
            "properties": {
                "Title": {"title": [{"text": {"content": body.get("title", "")}}]},
                "Year": {"number": body.get("year")},
                "Rating": {"number": body.get("rating")},
                "Review": {"rich_text": [{"text": {"content": body.get("review_text", "")}}]},
                "Date Watched": {"date": {"start": body.get("watched_at", "")[:10]}},
                "Poster URL": {"url": body.get("poster_url") or None},
            }
        }).encode()
        req = urllib.request.Request(
            "https://api.notion.com/v1/pages",
            data=notion_body,
            headers={
                "Authorization": f"Bearer {NOTION_TOKEN}",
                "Content-Type": "application/json",
                "Notion-Version": "2022-06-28",
            },
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req)
            self._relay(200, resp.read(), "application/json")
        except urllib.error.HTTPError as e:
            self._relay(e.code, e.read(), "application/json")

    # ---- TMDB proxy ----

    _TMDB_ROUTES = {
        "trending": "/trending/movie/week",
        "now-playing": "/movie/now_playing",
        "popular": "/movie/popular",
    }

    def _proxy_tmdb(self):
        if not TMDB_API_KEY:
            return self._relay(503, json.dumps({"error": "TMDB_API_KEY not configured"}).encode(), "application/json")
        key = self.path[len("/api/tmdb/"):]
        tmdb_path = self._TMDB_ROUTES.get(key)
        if not tmdb_path:
            return self.send_error(404)
        url = f"{TMDB_BASE}{tmdb_path}?api_key={TMDB_API_KEY}&region=US"
        try:
            req = urllib.request.Request(url)
            resp = urllib.request.urlopen(req)
            self._relay(200, resp.read(), "application/json")
        except urllib.error.HTTPError as e:
            self._relay(e.code, e.read(), "application/json")
        except urllib.error.URLError:
            self.send_error(502, "TMDB unreachable")

    # ---- TMDB recommendations & provider discovery ----

    _TMDB_PROVIDERS = {"netflix": 8, "max": 384, "hulu": 15}

    def _tmdb_recommendations(self):
        """Return personalized recommendations based on Radarr library."""
        if not TMDB_API_KEY:
            return self._relay(503, json.dumps({"error": "TMDB_API_KEY not configured"}).encode(), "application/json")
        # Fetch Radarr library
        try:
            req = urllib.request.Request(
                f"{RADARR_URL}/api/v3/movie",
                headers={"X-Api-Key": RADARR_API_KEY},
            )
            resp = urllib.request.urlopen(req)
            library = json.loads(resp.read())
        except Exception:
            return self._relay(200, json.dumps({"results": []}).encode(), "application/json")

        # Collect TMDB IDs of movies with files
        lib_ids = [m["tmdbId"] for m in library if m.get("hasFile") and m.get("tmdbId")]
        if len(lib_ids) < 2:
            return self._relay(200, json.dumps({"results": []}).encode(), "application/json")

        lib_id_set = set(lib_ids)
        seeds = random.sample(lib_ids, min(5, len(lib_ids)))

        # Fetch recommendations for each seed (parallel)
        def fetch_rec(seed_id):
            url = f"{TMDB_BASE}/movie/{seed_id}/recommendations?api_key={TMDB_API_KEY}"
            resp = urllib.request.urlopen(url)
            return json.loads(resp.read()).get("results", [])

        seen = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(fetch_rec, sid): sid for sid in seeds}
            for future in concurrent.futures.as_completed(futures):
                try:
                    for movie in future.result():
                        mid = movie["id"]
                        if mid not in lib_id_set and mid not in seen:
                            seen[mid] = movie
                except Exception:
                    continue

        # Sort by quality-weighted popularity, take top 20
        results = sorted(seen.values(), key=lambda m: m.get("vote_average", 0) * m.get("vote_count", 0), reverse=True)[:20]
        self._relay(200, json.dumps({"results": results}).encode(), "application/json")

    def _tmdb_provider(self):
        """Return popular movies for a streaming provider."""
        if not TMDB_API_KEY:
            return self._relay(503, json.dumps({"error": "TMDB_API_KEY not configured"}).encode(), "application/json")
        key = self.path.split("/")[-1]
        provider_id = self._TMDB_PROVIDERS.get(key)
        if provider_id is None:
            return self.send_error(404, f"Unknown provider: {key}")
        url = (
            f"{TMDB_BASE}/discover/movie?api_key={TMDB_API_KEY}"
            f"&with_watch_providers={provider_id}&watch_region=US&sort_by=popularity.desc"
        )
        try:
            req = urllib.request.Request(url)
            resp = urllib.request.urlopen(req)
            self._relay(200, resp.read(), "application/json")
        except urllib.error.HTTPError as e:
            self._relay(e.code, e.read(), "application/json")
        except urllib.error.URLError:
            self.send_error(502, "TMDB unreachable")

    # ---- Bulk Import ----

    @staticmethod
    def _clean_folder_name(name):
        """Extract clean movie title from folder name for Radarr lookup."""
        # Strip trailing quality/codec info: "Movie.1080p.BluRay.x264.AAC-GROUP"
        name = re.sub(r'[\.\s](\d{3,4}p)[\.\s].*$', '', name)
        # Strip [1080p] or similar brackets
        name = re.sub(r'\s*\[.*?\]\s*', ' ', name)
        # Extract title before (YYYY) — keep the year for better lookup
        m = re.match(r'^(.+?)\s*\((\d{4})\)', name)
        if m:
            return f"{m.group(1).strip()} {m.group(2)}"
        # Replace dots with spaces (for dotted names)
        name = name.replace('.', ' ')
        return name.strip()

    def _import_library(self):
        """Scan ~/Downloads/Movies, add missing folders to Radarr."""
        if not MOVIES_DIR.is_dir():
            return self._relay(400, json.dumps(
                {"error": f"Movies directory not found: {MOVIES_DIR}"}
            ).encode(), "application/json")

        # Get existing Radarr movies, index by folder base name
        try:
            req = urllib.request.Request(
                f"{RADARR_URL}/api/v3/movie",
                headers={"X-Api-Key": RADARR_API_KEY},
            )
            resp = urllib.request.urlopen(req)
            existing = json.loads(resp.read())
        except Exception as e:
            return self._relay(502, json.dumps(
                {"error": f"Radarr unreachable: {e}"}
            ).encode(), "application/json")

        existing_paths = set()
        for mov in existing:
            p = mov.get("path", "")
            existing_paths.add(p.rstrip("/").split("/")[-1])

        # Scan host directories
        folders = sorted(
            d.name for d in MOVIES_DIR.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        )

        added = 0
        skipped = 0
        failed = []

        for folder in folders:
            if folder in existing_paths:
                skipped += 1
                continue

            clean = self._clean_folder_name(folder)
            # Lookup via Radarr
            try:
                lookup_url = (
                    f"{RADARR_URL}/api/v3/movie/lookup"
                    f"?term={urllib.parse.quote(clean)}"
                )
                req = urllib.request.Request(
                    lookup_url, headers={"X-Api-Key": RADARR_API_KEY}
                )
                resp = urllib.request.urlopen(req)
                results = json.loads(resp.read())
            except Exception as e:
                failed.append({"folder": folder, "error": f"lookup error: {e}"})
                continue

            if not results:
                failed.append({"folder": folder, "error": "no results found"})
                continue

            hit = results[0]
            add_body = json.dumps({
                "title": hit["title"],
                "tmdbId": hit["tmdbId"],
                "titleSlug": hit["titleSlug"],
                "images": hit.get("images", []),
                "qualityProfileId": 4,
                "rootFolderPath": "/movies",
                "path": f"/movies/{folder}",
                "monitored": True,
                "minimumAvailability": "released",
                "addOptions": {"searchForMovie": False},
            }).encode()

            try:
                req = urllib.request.Request(
                    f"{RADARR_URL}/api/v3/movie",
                    data=add_body,
                    headers={
                        "X-Api-Key": RADARR_API_KEY,
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                urllib.request.urlopen(req)
                added += 1
            except urllib.error.HTTPError as e:
                err_body = e.read().decode(errors="replace")
                if "already" in err_body.lower():
                    skipped += 1
                else:
                    failed.append({"folder": folder, "error": err_body[:200]})
            except Exception as e:
                failed.append({"folder": folder, "error": str(e)})

        result = {"added": added, "skipped": skipped, "failed": failed}
        self._relay(200, json.dumps(result).encode(), "application/json")

    # ---- Plex Sync ----

    def _plex_sync_watched(self):
        """Fetch Plex watched movies, insert into local watched table."""
        if not PLEX_TOKEN:
            return self._relay(400, json.dumps(
                {"error": "PLEX_TOKEN not configured in .env"}
            ).encode(), "application/json")

        # Fetch all movies with GUIDs
        try:
            url = (
                f"{PLEX_URL}/library/sections/1/all"
                f"?X-Plex-Token={PLEX_TOKEN}&includeGuids=1"
            )
            req = urllib.request.Request(url)
            resp = urllib.request.urlopen(req)
            xml_data = resp.read()
        except Exception as e:
            return self._relay(502, json.dumps(
                {"error": f"Plex unreachable: {e}"}
            ).encode(), "application/json")

        root = ET.fromstring(xml_data)
        synced = 0
        total_watched = 0

        conn = get_db()
        for video in root.iter("Video"):
            view_count = int(video.get("viewCount", "0"))
            if view_count == 0:
                continue
            total_watched += 1

            # Extract TMDB ID from Guid children
            tmdb_id = None
            for guid in video.findall("Guid"):
                gid = guid.get("id", "")
                if gid.startswith("tmdb://"):
                    tmdb_id = int(gid[7:])
                    break
            if not tmdb_id:
                continue

            title = video.get("title", "")
            year = int(video.get("year", "0")) or None
            # Convert lastViewedAt (epoch) to ISO datetime
            last_viewed = video.get("lastViewedAt", "")
            watched_at = None
            if last_viewed:
                watched_at = datetime.datetime.fromtimestamp(
                    int(last_viewed)
                ).strftime("%Y-%m-%d %H:%M:%S")

            try:
                cur = conn.execute("""
                    INSERT INTO watched (tmdb_id, title, year, watched_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(tmdb_id) DO NOTHING
                """, (tmdb_id, title, year, watched_at))
                if cur.rowcount > 0:
                    synced += 1
            except Exception:
                pass

        conn.commit()
        conn.close()

        # Second pass: import Plex watched movies missing from Radarr
        radarr_added = 0
        plex_tmdb_movies = []
        for video in root.iter("Video"):
            if int(video.get("viewCount", "0")) == 0:
                continue
            tmdb_id = None
            for guid in video.findall("Guid"):
                gid = guid.get("id", "")
                if gid.startswith("tmdb://"):
                    tmdb_id = int(gid[7:])
                    break
            if tmdb_id:
                plex_tmdb_movies.append(tmdb_id)

        if plex_tmdb_movies:
            try:
                req = urllib.request.Request(
                    f"{RADARR_URL}/api/v3/movie",
                    headers={"X-Api-Key": RADARR_API_KEY},
                )
                resp = urllib.request.urlopen(req)
                radarr_lib = json.loads(resp.read())
                radarr_ids = {m["tmdbId"] for m in radarr_lib if m.get("tmdbId")}
            except Exception:
                radarr_ids = None  # skip import if Radarr unreachable

            if radarr_ids is not None:
                missing = [tid for tid in plex_tmdb_movies if tid not in radarr_ids]
                for tid in missing:
                    try:
                        lookup_req = urllib.request.Request(
                            f"{RADARR_URL}/api/v3/movie/lookup/tmdb?tmdbId={tid}",
                            headers={"X-Api-Key": RADARR_API_KEY},
                        )
                        lookup_resp = urllib.request.urlopen(lookup_req)
                        hit = json.loads(lookup_resp.read())
                        add_body = json.dumps({
                            "title": hit.get("title", ""),
                            "tmdbId": hit["tmdbId"],
                            "titleSlug": hit.get("titleSlug", ""),
                            "images": hit.get("images", []),
                            "qualityProfileId": 4,
                            "rootFolderPath": "/movies",
                            "monitored": True,
                            "minimumAvailability": "released",
                            "addOptions": {"searchForMovie": False},
                        }).encode()
                        add_req = urllib.request.Request(
                            f"{RADARR_URL}/api/v3/movie",
                            data=add_body,
                            headers={
                                "X-Api-Key": RADARR_API_KEY,
                                "Content-Type": "application/json",
                            },
                            method="POST",
                        )
                        urllib.request.urlopen(add_req)
                        radarr_added += 1
                    except Exception:
                        pass

        result = {"synced": synced, "total_watched": total_watched, "radarr_added": radarr_added}
        self._relay(200, json.dumps(result).encode(), "application/json")

    def _plex_scan_library(self):
        """Trigger a Plex library scan for the Movies section."""
        if not PLEX_TOKEN:
            return self._relay(400, json.dumps(
                {"error": "PLEX_TOKEN not configured in .env"}
            ).encode(), "application/json")
        try:
            url = (
                f"{PLEX_URL}/library/sections/1/refresh"
                f"?X-Plex-Token={PLEX_TOKEN}"
            )
            req = urllib.request.Request(url)
            urllib.request.urlopen(req)
            self._relay(200, json.dumps({"ok": True}).encode(), "application/json")
        except urllib.error.HTTPError as e:
            self._relay(e.code, e.read(), "application/json")
        except urllib.error.URLError as e:
            self._relay(502, json.dumps(
                {"error": f"Plex unreachable: {e}"}
            ).encode(), "application/json")

    # ---- Chat ----

    def _build_chat_context(self):
        """Build system prompt with Radarr library and watched history."""
        lines = [
            "You are a helpful movie recommendation assistant for the Reelz app.",
            "You have access to the user's movie library and watch history.",
            "Use this context to give personalized recommendations.",
            "Be concise and conversational.",
            "",
            "## User's Downloaded Library",
        ]
        # Fetch Radarr library
        try:
            req = urllib.request.Request(
                f"{RADARR_URL}/api/v3/movie",
                headers={"X-Api-Key": RADARR_API_KEY},
            )
            resp = urllib.request.urlopen(req)
            library = json.loads(resp.read())
            downloaded = [m for m in library if m.get("hasFile")]
            if downloaded:
                for m in sorted(downloaded, key=lambda x: x.get("title", "")):
                    lines.append(f"- {m.get('title', '?')} ({m.get('year', '?')})")
            else:
                lines.append("(empty)")
        except Exception:
            lines.append("(unavailable)")

        lines.append("")
        lines.append("## Watched & Rated Movies")
        # Fetch watched from SQLite
        try:
            conn = get_db()
            rows = conn.execute(
                "SELECT title, year, rating, review_text FROM watched ORDER BY watched_at DESC"
            ).fetchall()
            conn.close()
            if rows:
                for r in rows:
                    entry = f"- {r['title']} ({r['year'] or '?'})"
                    if r["rating"]:
                        entry += f" -- rated {r['rating']}/5"
                    if r["review_text"]:
                        entry += f" ({r['review_text']})"
                    lines.append(entry)
            else:
                lines.append("(none yet)")
        except Exception:
            lines.append("(unavailable)")

        return "\n".join(lines)

    def _chat(self):
        """Stream Claude chat responses via SSE."""
        if not ANTHROPIC_API_KEY:
            return self._relay(
                400,
                json.dumps({"error": "Add ANTHROPIC_API_KEY to .env"}).encode(),
                "application/json",
            )

        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n)) if n else {}
        messages = body.get("messages", [])

        if not isinstance(messages, list) or not messages:
            return self._relay(400, json.dumps({"error": "messages array required"}).encode(), "application/json")

        system_prompt = self._build_chat_context()

        api_body = json.dumps({
            "model": ANTHROPIC_MODEL,
            "max_tokens": 1024,
            "system": system_prompt,
            "messages": messages,
            "stream": True,
        }).encode()

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=api_body,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            resp = urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as e:
            err_body = e.read()
            return self._relay(e.code, err_body, "application/json")
        except urllib.error.URLError as e:
            return self._relay(
                502,
                json.dumps({"error": f"Anthropic API unreachable: {e}"}).encode(),
                "application/json",
            )

        # Stream SSE to client
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        try:
            while True:
                line = resp.readline()
                if not line:
                    break
                self.wfile.write(line)
                self.wfile.flush()
        except BrokenPipeError:
            pass
        finally:
            resp.close()

    def _relay(self, status, data, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        # Only log errors, not every 200
        if args and str(args[1]).startswith("2"):
            return
        super().log_message(fmt, *args)


if __name__ == "__main__":
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Reelz server running at http://localhost:{PORT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        srv.server_close()
