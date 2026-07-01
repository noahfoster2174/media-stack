#!/usr/bin/env python3
"""Reelz supervisor — the brain that keeps the rig healthy.

Probes every dependency Reelz needs, heals what it can, and caches the result so the
UI/menubar reads are instant. Stdlib only. This is the single source of truth behind
`/api/health` and `/api/heal`, generalizing what `ensure_services()` did ad hoc.

State model per service: "up" | "starting" (down but healed recently) | "down".
Overall: "up" (all up) | "degraded" (only non-critical down) | "down" (a critical is down).
"""

import json
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # ~/media-stack
ENV_FILE = ROOT / ".env"
HEAL_COOLDOWN_S = 45          # don't re-heal the same service more often than this
STARTING_GRACE_S = 30         # a down service healed within this window shows as "starting"


def _load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


_ENV = _load_env()
OLLAMA_MODEL = _ENV.get("OLLAMA_MODEL", "llama3.2:3b")
QBT_USER = _ENV.get("QBT_USER", "admin")
QBT_PASS = _ENV.get("QBT_PASS", "")


# ---- low-level helpers ----

def _http_up(url, timeout=3):
    """True if the endpoint answers at all (any HTTP status, even 401/403) → it's listening."""
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True            # a 4xx still means the service is up and responding
    except Exception:
        return False


def _run(args, timeout=15):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))
    except Exception:
        return None


def _compose(*args, timeout=120):
    return _run(["docker", "compose", *args], timeout=timeout)


# ---- probes ----

def _probe_docker():
    r = _run(["docker", "info"], timeout=8)
    return bool(r and r.returncode == 0)


def _probe_mullvad():
    r = _run(["mullvad", "status"], timeout=8)
    return bool(r and "Connected" in (r.stdout or ""))


# ---- heals (idempotent, best-effort, timeout-bounded) ----

def _heal_docker():
    _run(["open", "-a", "Docker"], timeout=8)
    for _ in range(30):
        if _probe_docker():
            break
        time.sleep(2)
    _compose("up", "-d")


def _heal_restart(svc_name):
    def _h():
        _compose("restart", svc_name, timeout=60)
    return _h


def _heal_mullvad():
    _run(["mullvad", "connect"], timeout=15)


def _ollama_warm():
    """Load + pin the chat model (keep_alive=-1) so it stays resident in memory."""
    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=json.dumps({"model": OLLAMA_MODEL, "keep_alive": -1}).encode())
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def _heal_ollama():
    # If the whole Ollama server is down, start it (brew service) before pinning the model.
    if not _http_up("http://localhost:11434/api/tags"):
        _run(["brew", "services", "start", "ollama"], timeout=20)
        for _ in range(10):
            if _http_up("http://localhost:11434/api/tags"):
                break
            time.sleep(1)
    _ollama_warm()


# ---- download-complete notifications ----

_qbt_sid = None
_done_hashes = None   # None until seeded on first check (so we don't notify existing downloads)


def _qbt_login():
    global _qbt_sid
    data = urllib.parse.urlencode({"username": QBT_USER, "password": QBT_PASS}).encode()
    try:
        resp = urllib.request.urlopen(
            urllib.request.Request("http://localhost:8080/api/v2/auth/login", data=data), timeout=5)
        for k, v in resp.getheaders():
            if k.lower() == "set-cookie" and "SID=" in v:
                _qbt_sid = v.split("SID=")[1].split(";")[0]
                return True
    except Exception:
        pass
    return False


def _qbt_torrents():
    global _qbt_sid
    if not _qbt_sid and not _qbt_login():
        return None
    for attempt in range(2):
        try:
            req = urllib.request.Request("http://localhost:8080/api/v2/torrents/info")
            req.add_header("Cookie", f"SID={_qbt_sid}")
            return json.loads(urllib.request.urlopen(req, timeout=5).read())
        except urllib.error.HTTPError as e:
            if e.code == 403 and attempt == 0 and _qbt_login():
                continue
            return None
        except Exception:
            return None
    return None


def _new_completions(prev_done, torrents):
    """Pure: given the previously-seen done hashes and current torrents, return
    (newly-completed torrents, updated done-hash set). prev_done=None seeds silently."""
    done_now = {t["hash"] for t in torrents if t.get("progress", 0) >= 1.0}
    if prev_done is None:
        return [], done_now
    new = [t for t in torrents if t.get("progress", 0) >= 1.0 and t["hash"] not in prev_done]
    return new, done_now


def notify(title, message):
    # ensure_ascii=False so emoji/Unicode pass as real UTF-8 (osascript can't parse \uXXXX).
    t = json.dumps(title, ensure_ascii=False)
    m = json.dumps(message, ensure_ascii=False)
    try:
        subprocess.run(["osascript", "-e", f"display notification {m} with title {t}"], timeout=5)
    except Exception:
        pass


def check_downloads():
    """Poll qBittorrent; fire a notification for each newly-finished download."""
    global _done_hashes
    torrents = _qbt_torrents()
    if torrents is None:
        return
    new, _done_hashes = _new_completions(_done_hashes, torrents)
    for t in new:
        notify("Reelz", f"\U0001F37F {t.get('name', 'A download')} is ready to watch")


_last_warm = 0.0

def keep_model_warm():
    """Re-pin keep_alive=-1 every ~4 min so a chat's default 5-min timeout can't unload the
    model. Robust alternative to editing Ollama's brew plist (which brew regenerates)."""
    global _last_warm
    if time.time() - _last_warm < 240:
        return
    if _http_up("http://localhost:11434/api/tags"):
        _last_warm = time.time()
        _ollama_warm()


# ---- registry ----

class Service:
    def __init__(self, name, label, probe, heal=None, critical=False, hint=""):
        self.name = name
        self.label = label
        self.probe = probe
        self.heal = heal
        self.critical = critical      # critical → blocks the app's "ready" gate
        self.hint = hint              # shown when down


SERVICES = [
    Service("docker", "Docker", _probe_docker, _heal_docker, critical=True,
            hint="Docker Desktop isn't running."),
    Service("qbittorrent", "qBittorrent", lambda: _http_up("http://localhost:8080/"),
            _heal_restart("qbittorrent"), critical=True,
            hint="Download client is down."),
    Service("radarr", "Radarr", lambda: _http_up("http://localhost:7878/ping"),
            _heal_restart("radarr"), critical=True,
            hint="Movie service is down."),
    Service("prowlarr", "Prowlarr", lambda: _http_up("http://localhost:9696/ping"),
            _heal_restart("prowlarr"),
            hint="Indexer manager is down (search may be limited)."),
    Service("sonarr", "Sonarr", lambda: _http_up("http://localhost:8989/ping"),
            _heal_restart("sonarr"),
            hint="TV service is down."),
    Service("mullvad", "Mullvad VPN", _probe_mullvad, _heal_mullvad,
            hint="VPN disconnected — downloads route through it."),
    Service("ollama", "Chat model", lambda: _http_up("http://localhost:11434/api/tags"),
            _heal_ollama,
            hint="Local chat backend is down."),
]

_state = {"overall": "unknown", "services": [], "ts": 0}
_last_heal = {}
_lock = threading.Lock()
_thread = None


def _status_of(svc, up):
    if up:
        return "up"
    if time.time() - _last_heal.get(svc.name, 0) < STARTING_GRACE_S:
        return "starting"
    return "down"


def _snapshot_service(svc):
    try:
        up = bool(svc.probe())
    except Exception:
        up = False
    status = _status_of(svc, up)
    return {
        "name": svc.name,
        "label": svc.label,
        "status": status,
        "critical": svc.critical,
        "can_heal": svc.heal is not None,
        "hint": "" if up else svc.hint,
    }


def check_all(heal=False):
    """Probe every service; optionally heal any that are down (respecting cooldown)."""
    results = []
    for svc in SERVICES:
        r = _snapshot_service(svc)
        if heal and r["status"] == "down" and svc.heal:
            if time.time() - _last_heal.get(svc.name, 0) >= HEAL_COOLDOWN_S:
                _last_heal[svc.name] = time.time()
                try:
                    svc.heal()
                except Exception:
                    pass
                r = _snapshot_service(svc)   # re-probe (likely "starting" now)
        results.append(r)

    if any(r["status"] != "up" and r["critical"] for r in results):
        overall = "down"
    elif any(r["status"] != "up" for r in results):
        overall = "degraded"
    else:
        overall = "up"

    snap = {"overall": overall, "services": results, "ts": int(time.time())}
    with _lock:
        _state.clear()
        _state.update(snap)
    return snap


def get_state():
    """Return the cached state (or compute once if the loop hasn't run yet)."""
    with _lock:
        if _state.get("services"):
            return dict(_state)
    return check_all()


def heal(name):
    """Heal one service on demand (from the UI/menubar Fix action)."""
    svc = next((s for s in SERVICES if s.name == name), None)
    if not svc:
        return {"ok": False, "error": f"unknown service: {name}"}
    if not svc.heal:
        return {"ok": False, "error": f"{svc.label} has no automatic fix"}
    _last_heal[svc.name] = time.time()
    try:
        svc.heal()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "service": _snapshot_service(svc)}


def start(interval=5, auto_heal=True):
    """Start the background probe/heal loop (idempotent)."""
    global _thread
    if _thread and _thread.is_alive():
        return
    def loop():
        while True:
            try:
                check_all(heal=auto_heal)
            except Exception:
                pass
            try:
                check_downloads()
            except Exception:
                pass
            try:
                keep_model_warm()
            except Exception:
                pass
            time.sleep(interval)
    _thread = threading.Thread(target=loop, name="reelz-supervisor", daemon=True)
    _thread.start()


if __name__ == "__main__":
    # Manual probe for debugging: `python3 supervisor.py`
    print(json.dumps(check_all(), indent=2))
