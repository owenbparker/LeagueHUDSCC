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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import outputs
from .config import DATA_DIR, LOGO_DIR, WEB_DIR, web_file
from .gold import DataDragon
from .log import log, since
from .source import DemoSource, LiveClientSource
from .state import build_state

mimetypes.add_type("font/otf", ".otf")
mimetypes.add_type("font/ttf", ".ttf")

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
        state = build_state(self.raw, self.config.get(), self.ddragon)
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
        for name in state["detail"]["unknown_events"]:
            if name not in self._seen_unknown:
                self._seen_unknown.add(name)
                log(f"New game event not used yet: {name}", "warn")

    def run_poller(self):
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception as e:  # keep the HUD alive whatever the game sends
                log(f"Update failed: {e!r}", "error")
            self._stop.wait(float(self.config.get()["poll_interval"]))

    def stop(self):
        self._stop.set()
        if self.server:
            self.server.stop()

    def app_info(self):
        cfg = self.config.get()
        port = self.server.port if self.server else cfg["http_port"]
        network = bool(cfg.get("allow_network"))
        lan = lan_ip() if network else None
        return {"mode": self.mode, "demo_speed": self.demo_speed, "network": network, "port": port,
                "lan_url": f"http://{lan}:{port}" if lan else None, "data_dir": DATA_DIR}

    def snapshot(self):
        with self._lock:
            return {"fields": self.state["fields"], "detail": self.state["detail"],
                    "updated_at": self.updated_at, "config": self.config.get(),
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
            path = web_file(rel)
            if not path:
                return self._send('{"error":"not found"}', status=404)
            with open(path, "rb") as f:
                body = f.read()
            self._send(body, mimetypes.guess_type(path)[0] or "application/octet-stream")

        def do_GET(self):
            url = urlparse(self.path)
            path = url.path
            fields = app.snapshot()["fields"]
            if path == "/":
                self.send_response(302)
                self.send_header("Location", "/control")
                self.end_headers()
            elif path in ("/overlay", "/control"):
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
            else:
                self._static(path.lstrip("/"))

        def do_POST(self):
            # Other PCs may read the HUD (when allowed), but only this PC can
            # change it.
            if not _is_local(self.client_address[0]):
                return self._send('{"error":"Changes can only be made on the HUD PC"}', status=403)
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
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
    threading.Thread(target=app.run_poller, daemon=True).start()
    log(f"League HUD running - control panel http://localhost:{port}/control")
    log(f"Overlay (browser source, 1920x1080): http://localhost:{port}/overlay")
    log(f"vMix data source: http://localhost:{port}/hud.csv  - file: {app.config.output_dir()}")
    return app.server
