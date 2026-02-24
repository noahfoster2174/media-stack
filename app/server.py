#!/usr/bin/env python3
"""Proxy server for Download Movie web app. Stdlib only, no pip installs."""

import http.server
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

PORT = 9999
RADARR_URL = "http://localhost:7878"
RADARR_API_KEY = "REDACTED_API_KEY"
QBT_URL = "http://localhost:8080"
QBT_USER = "admin"
QBT_PASS = "REDACTED_PASSWORD"

qbt_sid = None


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
        if self.path.startswith("/api/radarr/"):
            self._proxy_radarr()
        else:
            self.send_error(404)

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
