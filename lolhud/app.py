"""The running app: polls the game, keeps the latest HUD state, serves the
overlay and control panel over HTTP."""

import base64
import json
import mimetypes
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import outputs
from .config import DATA_DIR, LOGO_DIR, MEDIA_DIR, WEB_DIR, web_file
from .gold import DataDragon
from .log import log, since
from .ocr import ScreenGold
from .updater import Updater, clean_up_old_update
from . import profiles
from .source import DemoSource, LiveClientSource
from .state import build_state

mimetypes.add_type("font/otf", ".otf")
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("video/mp4", ".m4v")
mimetypes.add_type("video/webm", ".webm")

# Bundled logos (shipped with the app) plus ones you've uploaded.
BUNDLED_LOGO_DIR = os.path.join(WEB_DIR, "assets", "logos")


class HudApp:
    def __init__(self, config, source=None, mode="live"):
        self.config = config
        cfg = config.get()
        self.mode = mode
        self.demo_speed = getattr(source, "speed", 8.0)
        self.source = source or LiveClientSource(cfg["api_url"])
        self.ddragon = DataDragon(config.output_dir())
        self.writer = outputs.FileWriter(config.output_dir())
        self.raw = None
        self.state = build_state(None, cfg, self.ddragon)
        self.updated_at = 0.0
        self.server = None
        self.screen_gold = ScreenGold(config)
        self.updater = Updater()
        self.overlay_window = None  # the desktop app's HUD-over-the-game window
        # "gfx" runs the HUD; "observer" only shows another PC's HUD over the
        # game here, so it stops reading the game. None until one is picked.
        self.role = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._was_connected = None
        self._last_error = None
        self._seen_unknown = set()

    def set_mode(self, mode, speed=None):
        """Switch between the real game ("live") and a simulated one ("demo")."""
        if speed:
            self.demo_speed = float(speed)
        if mode == "demo":
            self.source = DemoSource(speed=self.demo_speed)
            log(f"Demo game started ({self.demo_speed:g}x speed)")
        elif mode == "live":
            self.source = LiveClientSource(self.config.get()["api_url"])
            log("Reading the live game")
        else:
            raise ValueError(f"Unknown mode {mode!r}")
        self.mode = mode
        self._was_connected = None
        self.refresh()

    def refresh(self, fetch=True):
        if fetch:
            self.raw = self.source.fetch()
        live = self.mode == "live"
        state = build_state(self.raw, self.config.get(), self.ddragon,
                            self.screen_gold.fresh() if live else None,
                            self.screen_gold.fresh_counts() if live else None)
        state["detail"]["error"] = self.source.last_error
        with self._lock:
            self.state = state
            self.updated_at = time.time()
        self.writer.write(state["fields"])
        if fetch:
            self._log_changes(state)
        return state

    def _log_changes(self, state):
        connected = state["detail"]["connected"]
        if connected != self._was_connected and not connected:
            self.screen_gold.reset()  # next game starts from scratch
            self._tower_warned = False
        if connected != self._was_connected:
            if connected:
                log(f"Game connected ({state['fields']['TIMER']})")
            elif self._was_connected is not None:
                log("Game disconnected - waiting for the next game")
            self._was_connected = connected
        error = self.source.last_error
        if error and error != self._last_error:
            log(error, "warn")
        self._last_error = error
        detail = state["detail"]
        mismatch = {t: v for t, v in detail.get("tower_check", {}).items() if v[0] != v[1]}
        if mismatch and not getattr(self, "_tower_warned", False):
            self._tower_warned = True
            log("Tower events disagree with the top bar (using the top bar): "
                + ", ".join(f"{t} events {e} vs bar {b}" for t, (e, b) in mismatch.items())
                + f". Tower events seen: {detail.get('turret_events')}", "warn")
        events = {e.get("EventName"): e for e in ((self.raw or {}).get("events") or {}).get("Events") or []}
        for name in state["detail"]["unknown_events"]:
            if name not in self._seen_unknown:
                self._seen_unknown.add(name)
                if "(team unknown)" in name:
                    log(f"Couldn't tell which team took: {name}", "warn")
                else:
                    # Include one example, so the HUD can learn to use it.
                    log(f"New game event not used yet: {name} {json.dumps(events.get(name, {}))}", "warn")

    def set_role(self, role):
        if role not in ("gfx", "observer", None):
            raise ValueError(f"Unknown client: {role}")
        if role != self.role and self.overlay_window and self.overlay_window.window:
            self.overlay_window.close()
        if self.overlay_window:
            self.overlay_window.url = None
        self.role = role
        self.screen_gold.paused = role == "observer"
        if role:
            log({"gfx": "GFX Controller", "observer": "Observer Client"}[role] + " started")

    def run_poller(self):
        while not self._stop.is_set():
            if self.role == "observer":  # the GFX PC reads the game, not this one
                self._stop.wait(1)
                continue
            try:
                self.refresh()
            except Exception as e:  # keep the HUD alive whatever the game sends
                log(f"Update failed: {e!r}", "error")
            self._stop.wait(float(self.config.get()["poll_interval"]))

    def stop(self):
        self._stop.set()
        self.screen_gold.stop()
        if self.server:
            self.server.stop()

    def app_info(self):
        cfg = self.config.get()
        port = self.server.port if self.server else cfg["http_port"]
        network = bool(cfg.get("allow_network"))
        lan = lan_ip() if network else None
        sg = self.screen_gold
        screen = {"available": sg.available, "status": sg.status, "values": sg.fresh(),
                  "age": round(time.time() - sg.read_at, 1) if sg.read_at else None}
        return {"mode": self.mode, "role": self.role, "demo_speed": self.demo_speed, "network": network, "port": port,
                "screen_gold": screen, "update": self.updater.info(),
                "overlay_window": self.overlay_window.info() if self.overlay_window else {"available": False},
                "lan_url": f"http://{lan}:{port}" if lan else None, "data_dir": DATA_DIR}

    def snapshot(self):
        with self._lock:
            return {"fields": self.state["fields"], "detail": self.state["detail"],
                    "updated_at": self.updated_at, "config": self.config.get(),
                    "profiles_version": profiles.version(),
                    "output_dir": self.config.output_dir(), "app": self.app_info()}

    def save_logo(self, side, filename, data_b64):
        name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(filename or "logo.png"))
        if os.path.splitext(name)[1].lower() not in (".png", ".jpg", ".jpeg", ".webp", ".svg"):
            raise ValueError("Logo must be a PNG, JPG, WEBP or SVG")
        os.makedirs(LOGO_DIR, exist_ok=True)
        with open(os.path.join(LOGO_DIR, name), "wb") as f:
            f.write(base64.b64decode(data_b64))
        self.config.update({side: {"logo": f"assets/logos/{name}"}})
        log(f"Logo uploaded for the {side} team: {name}")
        return f"assets/logos/{name}"

    def save_media(self, filename, stream, length):
        name = re.sub(r"[^A-Za-z0-9._ -]", "_", os.path.basename(filename or "background.mp4")).strip() or "background.mp4"
        if os.path.splitext(name)[1].lower() not in (".mp4", ".webm", ".m4v"):
            raise ValueError("Background video must be an .mp4 or .webm file")
        if not 0 < length <= 1024 ** 3:
            raise ValueError("Background video must be under 1 GB")
        os.makedirs(MEDIA_DIR, exist_ok=True)
        tmp = os.path.join(MEDIA_DIR, name + ".part")
        with open(tmp, "wb") as f:
            left = length
            while left > 0:
                chunk = stream.read(min(1024 * 1024, left))
                if not chunk:
                    raise ValueError("Upload was cut short")
                f.write(chunk)
                left -= len(chunk)
        os.replace(tmp, os.path.join(MEDIA_DIR, name))
        return f"media/{name}"

    def list_logos(self):
        names = set()
        for folder in (BUNDLED_LOGO_DIR, LOGO_DIR):
            if os.path.isdir(folder):
                names.update(f for f in os.listdir(folder) if not f.startswith("."))
        return [f"assets/logos/{n}" for n in sorted(names)]

    def set_network(self, enabled):
        self.config.update({"allow_network": bool(enabled)})
        if self.server:
            self.server.restart(self.host())
        log("Other PCs on your network can now read the HUD" if enabled else "HUD is now only reachable from this PC")

    def host(self):
        cfg = self.config.get()
        return "0.0.0.0" if cfg.get("allow_network") else cfg["http_host"]

    def open_folder(self, which):
        folders = {"output": self.config.output_dir(), "logos": LOGO_DIR, "app": DATA_DIR}
        path = folders[which]
        os.makedirs(path, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(path)  # noqa: only exists on Windows
        else:
            subprocess.Popen(["xdg-open" if sys.platform != "darwin" else "open", path])


def parse_range(header, size):
    """'bytes=100-' -> (100, size - 1); None if absent or unusable."""
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", (header or "").strip())
    if not m or size <= 0 or (not m.group(1) and not m.group(2)):
        return None
    if m.group(1):
        start = int(m.group(1))
        end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    else:  # last N bytes
        start, end = max(0, size - int(m.group(2))), size - 1
    return (start, end) if start <= end < size else None


def check_gfx(text):
    """Observer client: is the GFX PC's HUD reachable at this address?"""
    from .overlay_window import gfx_base_url
    try:
        base = gfx_base_url(text)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    try:
        # Straight to the GFX PC on the network, never through a web proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(base + "/api/state", timeout=3) as r:
            ok = "fields" in json.loads(r.read())
    except Exception as e:
        reason = getattr(e, "reason", e)
        return {"ok": False, "error": f"Can't reach the HUD at {base} ({reason}). Check the address, that League HUD is "
                                      "running there, and that \"Let other PCs on the network read the HUD\" is ticked."}
    if not ok:
        return {"ok": False, "error": f"{base} isn't a League HUD"}
    return {"ok": True, "overlay": base + "/overlay"}


def lan_ip():
    """This PC's address on the local network (no traffic is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def _is_local(address):
    return address in ("127.0.0.1", "::1") or address.startswith("127.")


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, body, ctype="application/json", status=200):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _static(self, rel):
            path = web_file(unquote(rel))
            if not path:
                return self._send('{"error":"not found"}', status=404)
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            size = os.path.getsize(path)
            span = parse_range(self.headers.get("Range"), size)
            if span is None and size < 8 * 1024 * 1024:
                with open(path, "rb") as f:
                    return self._send(f.read(), ctype)
            # Big files (background videos) are streamed, with byte ranges so
            # the browser can seek and loop.
            start, end = span or (0, size - 1)
            self.send_response(206 if span else 200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if span:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                with open(path, "rb") as f:
                    f.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = f.read(min(256 * 1024, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except (ConnectionError, OSError):
                pass  # the browser stopped reading (normal while seeking)

        def do_GET(self):
            url = urlparse(self.path)
            path = url.path
            fields = app.snapshot()["fields"]
            if path == "/":
                self.send_response(302)
                self.send_header("Location", "/control")
                self.end_headers()
            elif path == "/api/observer/check":
                # Observer client: can this PC reach the GFX PC's HUD?
                if not _is_local(self.client_address[0]):
                    return self._send('{"error":"forbidden"}', status=403)
                self._send(json.dumps(check_gfx((parse_qs(url.query).get("url") or [""])[0])))
            elif path in ("/overlay", "/control", "/start", "/observer"):
                self._static(path[1:] + ".html")
            elif path == "/api/state":
                self._send(json.dumps(app.snapshot()))
            elif path == "/api/log":
                after = int((parse_qs(url.query).get("after") or ["0"])[0])
                self._send(json.dumps(since(after)))
            elif path == "/hud.json":
                self._send(json.dumps([fields]))
            elif path == "/hud.csv":
                self._send(outputs.to_csv(fields), "text/csv; charset=utf-8")
            elif path == "/hud.xml":
                self._send(outputs.to_xml(fields), "application/xml; charset=utf-8")
            elif path == "/api/logos":
                self._send(json.dumps(app.list_logos()))
            elif path == "/api/profiles":
                self._send(json.dumps(profiles.listing()))
            elif path == "/api/profile":
                pid = (parse_qs(url.query).get("id") or [app.config.get().get("profile")])[0]
                data = profiles.load(pid) or profiles.load(profiles.DEFAULT_PROFILE)
                self._send(json.dumps(data))
            else:
                self._static(path.lstrip("/"))

        def do_POST(self):
            # Other PCs may read the HUD (when allowed), but only this PC can
            # change it.
            if not _is_local(self.client_address[0]):
                return self._send('{"error":"Changes can only be made on the HUD PC"}', status=403)
            url = urlparse(self.path)
            path = url.path
            length = int(self.headers.get("Content-Length") or 0)
            if path == "/api/media":
                # A background video, sent as raw bytes (too big for JSON).
                try:
                    name = (parse_qs(url.query).get("filename") or [""])[0]
                    saved = app.save_media(name, self.rfile, length)
                    app.config.update({"bg_video": saved})
                    log(f"Background video set: {os.path.basename(saved)} ({length / 1e6:.1f} MB)")
                    app.refresh(fetch=False)
                    return self._send(json.dumps(app.snapshot()))
                except ValueError as e:
                    return self._send(json.dumps({"error": str(e)}), status=400)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if path == "/api/config":
                    app.config.update(body)
                elif path == "/api/logo":
                    app.save_logo(body["side"], body.get("filename"), body["data"])
                elif path == "/api/mode":
                    app.set_mode(body["mode"], body.get("speed"))
                elif path == "/api/network":
                    app.set_network(body["enabled"])
                elif path == "/api/open":
                    app.open_folder(body["folder"])
                elif path == "/api/profiles/import":
                    from .psd_import import import_upload
                    log(f"Importing {body.get('filename')}...")
                    try:
                        report = import_upload(body.get("name"), body.get("filename", ""), base64.b64decode(body["data"]))
                    except ValueError:
                        raise
                    except Exception as e:  # a PSD the reader can't handle
                        log(f"PSD import failed: {e!r}", "error")
                        raise ValueError(f"Couldn't read that PSD: {e}")
                    app.config.update({"profile": report["id"]})
                    log(f"Imported HUD profile '{report['name']}': {len(report['fields'])} live fields"
                        + (f"; fonts to add: {', '.join(report['missing_fonts'])}" if report["missing_fonts"] else ""))
                    app.refresh(fetch=False)
                    return self._send(json.dumps({"report": report, **app.snapshot()}))
                elif path == "/api/profiles/font":
                    from .psd_import import add_font
                    result = add_font(body["id"], body.get("filename", ""), base64.b64decode(body["data"]))
                    log(f"Font added to '{body['id']}'" + (f" for {result['matched']}" if result["matched"] else ""))
                elif path == "/api/profiles/delete":
                    profiles.delete(body["id"])
                    if app.config.get().get("profile") == body["id"]:
                        app.config.update({"profile": profiles.DEFAULT_PROFILE})
                    log(f"Deleted HUD profile {body['id']}")
                elif path == "/api/update/check":
                    app.updater.check()
                elif path == "/api/update/install":
                    app.updater.install_in_background()
                elif path == "/api/overlay-window":
                    if not app.overlay_window:
                        raise ValueError("The HUD window is only available in the desktop app")
                    if body.get("url") is not None:
                        app.config.update({"observer_url": body["url"]})
                    app.overlay_window.set(bool(body.get("open")), body.get("screen"), body.get("url"))
                elif path == "/api/role":
                    app.set_role(body.get("role"))
                elif path == "/api/screen-gold/test":
                    return self._send(json.dumps(app.screen_gold.test()))
                else:
                    return self._send('{"error":"not found"}', status=404)
                app.refresh(fetch=False)
                self._send(json.dumps(app.snapshot()))
            except (ValueError, KeyError, OSError) as e:
                self._send(json.dumps({"error": str(e)}), status=400)

    return Handler


class Server:
    """The HTTP server in a background thread; can be restarted on a new host."""

    def __init__(self, app, host, port):
        self.app, self.port = app, port
        self.httpd = None
        self._start(host)

    def _start(self, host):
        self.httpd = ThreadingHTTPServer((host, self.port), make_handler(self.app))
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.host = host

    def restart(self, host):
        if host == self.host:
            return
        old = self.httpd
        old.shutdown()
        old.server_close()
        self._start(host)

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def start(app):
    """Start the web server and the game poller. Raises OSError if the port is taken."""
    port = app.config.get()["http_port"]
    app.server = Server(app, app.host(), port)
    app.screen_gold.start()
    clean_up_old_update()
    app.updater.check_in_background()
    threading.Thread(target=app.run_poller, daemon=True).start()
    log(f"League HUD {app.updater.current} running - control panel http://localhost:{port}/control")
    log(f"Overlay (browser source, 1920x1080): http://localhost:{port}/overlay")
    log(f"vMix data source: http://localhost:{port}/hud.csv  - file: {app.config.output_dir()}")
    return app.server
