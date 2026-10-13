"""The running app: polls the game, keeps the latest HUD state, serves the
overlay and control panel over HTTP."""

import base64
import json
import mimetypes
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import outputs
from .config import WEB_DIR
from .gold import DataDragon
from .source import LiveClientSource
from .state import build_state

mimetypes.add_type("font/otf", ".otf")
mimetypes.add_type("font/ttf", ".ttf")

LOGO_DIR = os.path.join(WEB_DIR, "assets", "logos")


class HudApp:
    def __init__(self, config, source=None):
        self.config = config
        cfg = config.get()
        self.source = source or LiveClientSource(cfg["api_url"])
        self.ddragon = DataDragon(config.output_dir())
        self.writer = outputs.FileWriter(config.output_dir())
        self.raw = None
        self.state = build_state(None, cfg, self.ddragon)
        self.updated_at = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def refresh(self, fetch=True):
        if fetch:
            self.raw = self.source.fetch()
        state = build_state(self.raw, self.config.get(), self.ddragon)
        state["detail"]["error"] = self.source.last_error
        with self._lock:
            self.state = state
            self.updated_at = time.time()
        self.writer.write(state["fields"])
        return state

    def run_poller(self):
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception as e:  # keep the HUD alive whatever the game sends
                print(f"[hud] update failed: {e!r}")
            self._stop.wait(float(self.config.get()["poll_interval"]))

    def stop(self):
        self._stop.set()

    def snapshot(self):
        with self._lock:
            return {"fields": self.state["fields"], "detail": self.state["detail"],
                    "updated_at": self.updated_at, "config": self.config.get(),
                    "output_dir": self.config.output_dir()}

    def save_logo(self, side, filename, data_b64):
        name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(filename or "logo.png"))
        if os.path.splitext(name)[1].lower() not in (".png", ".jpg", ".jpeg", ".webp", ".svg"):
            raise ValueError("Logo must be a PNG, JPG, WEBP or SVG")
        os.makedirs(LOGO_DIR, exist_ok=True)
        with open(os.path.join(LOGO_DIR, name), "wb") as f:
            f.write(base64.b64decode(data_b64))
        self.config.update({side: {"logo": f"assets/logos/{name}"}})
        return f"assets/logos/{name}"


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
            path = os.path.normpath(os.path.join(WEB_DIR, rel))
            if not path.startswith(WEB_DIR + os.sep) or not os.path.isfile(path):
                return self._send('{"error":"not found"}', status=404)
            with open(path, "rb") as f:
                body = f.read()
            self._send(body, mimetypes.guess_type(path)[0] or "application/octet-stream")

        def do_GET(self):
            path = urlparse(self.path).path
            fields = app.snapshot()["fields"]
            if path == "/":
                self.send_response(302)
                self.send_header("Location", "/control")
                self.end_headers()
            elif path in ("/overlay", "/control"):
                self._static(path[1:] + ".html")
            elif path == "/api/state":
                self._send(json.dumps(app.snapshot()))
            elif path == "/hud.json":
                self._send(json.dumps([fields]))
            elif path == "/hud.csv":
                self._send(outputs.to_csv(fields), "text/csv; charset=utf-8")
            elif path == "/hud.xml":
                self._send(outputs.to_xml(fields), "application/xml; charset=utf-8")
            elif path == "/api/logos":
                files = sorted(os.listdir(LOGO_DIR)) if os.path.isdir(LOGO_DIR) else []
                self._send(json.dumps([f"assets/logos/{f}" for f in files]))
            else:
                self._static(path.lstrip("/"))

        def do_POST(self):
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if path == "/api/config":
                    app.config.update(body)
                elif path == "/api/logo":
                    app.save_logo(body["side"], body.get("filename"), body["data"])
                else:
                    return self._send('{"error":"not found"}', status=404)
                app.refresh(fetch=False)
                self._send(json.dumps(app.snapshot()))
            except (ValueError, KeyError) as e:
                self._send(json.dumps({"error": str(e)}), status=400)

    return Handler


def serve(app, host, port):
    server = ThreadingHTTPServer((host, port), make_handler(app))
    threading.Thread(target=app.run_poller, daemon=True).start()
    return server
