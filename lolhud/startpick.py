"""The start screen's memory: which client this PC last opened (GFX Controller
or Observer Client) and whether to skip the screen and open it again next time.
Shared by every SCC HUD that has both clients. Copied verbatim into each app.

Saved in start.json next to the app's other settings (the app window keeps no
browser storage between runs).

    GET  /api/start            -> {"last": "gfx" | "observer" | null, "auto": false}
    POST /api/start            {"last": "observer"} and/or {"auto": true}
    GET  /start                -> 302 to /control or /observer when auto is on and a
                                  client was picked before; /start?pick=1 always shows it
"""

import ipaddress
import json
import os
import threading
from urllib.parse import parse_qs, urlparse

PAGES = {"gfx": "/control", "observer": "/observer"}
DEFAULT = {"last": None, "auto": False}
_lock = threading.Lock()


def _path(data_dir):
    return os.path.join(data_dir, "start.json")


def clean(s):
    return {"last": s.get("last") if s.get("last") in PAGES else None, "auto": s.get("auto") is True}


def load(data_dir):
    try:
        with open(_path(data_dir), encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        saved = {}
    return clean(dict(DEFAULT, **(saved if isinstance(saved, dict) else {})))


def save(data_dir, change):
    with _lock:
        s = load(data_dir)
        s.update({k: v for k, v in (change or {}).items() if k in DEFAULT})
        s = clean(s)
        os.makedirs(data_dir, exist_ok=True)
        tmp = _path(data_dir) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
        os.replace(tmp, _path(data_dir))
        return s


def skip_to(data_dir, query=""):
    """The client page /start should open straight away, or None to show the screen."""
    if "pick" in parse_qs(query):
        return None
    s = load(data_dir)
    return PAGES.get(s["last"]) if s["auto"] else None


def _local(ip):
    try:
        a = ipaddress.ip_address(ip)
        return a.is_loopback or (a.ipv4_mapped is not None and a.ipv4_mapped.is_loopback) if a.version == 6 else a.is_loopback
    except ValueError:
        return False


def _send(req, status, out):
    data = json.dumps(out).encode("utf-8")
    req.send_response(status)
    req.send_header("Content-Type", "application/json")
    req.send_header("Cache-Control", "no-store")
    req.send_header("Content-Length", str(len(data)))
    req.end_headers()
    req.wfile.write(data)


def handle(req, method, path, data_dir):
    """Answers /api/start, and /start when it should skip ahead. True when it did."""
    if method == "GET" and path == "/start":
        target = skip_to(data_dir, urlparse(req.path).query)
        if not target:
            return False
        req.send_response(302)
        req.send_header("Location", target)
        req.send_header("Cache-Control", "no-store")
        req.send_header("Content-Length", "0")
        req.end_headers()
        return True
    if path != "/api/start" or method not in ("GET", "POST"):
        return False
    try:
        if method == "POST" and not _local(req.client_address[0]):
            return _send(req, 403, {"error": "Only this PC can change this"}) or True
        if method == "POST":
            length = int(req.headers.get("Content-Length") or 0)
            body = json.loads(req.rfile.read(length) or b"{}") if length else {}
            out = save(data_dir, body if isinstance(body, dict) else {})
        else:
            out = load(data_dir)
        _send(req, 200, out)
    except (OSError, ValueError) as e:
        _send(req, 400, {"error": str(e)})
    return True
