#!/usr/bin/env python3
"""Proxy server for Download Movie web app. Stdlib only, no pip installs."""

import http.server
import json
import os
import subprocess
import urllib.request
import urllib.parse
import urllib.error
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

qbt_sid = None


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
        elif self.path.startswith("/api/radarr/"):
            self._proxy_radarr()
        elif self.path.startswith("/api/qbt/"):
            self._proxy_qbt()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/ensure-services":
            self._ensure_services()
        elif self.path.startswith("/api/radarr/"):
            self._proxy_radarr()
        elif self.path.startswith("/api/qbt/"):
            self._proxy_qbt_post()
        else:
            self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("/api/radarr/"):
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
    srv = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Download Movie server running at http://localhost:{PORT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        srv.server_close()
