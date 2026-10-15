"""The running app: polls the game, keeps the latest HUD state, serves the
overlay and control panel over HTTP."""

import base64
import json
import mimetypes
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import outputs
from .cams import CamHub, Director, get_json, post_json
from .config import DATA_DIR, LOGO_DIR, MEDIA_DIR, WEB_DIR, web_file
from .gold import DataDragon
from .log import log, since
from .matchtime import MatchSync, ReplayClient, enable_replay_api, run_follower
from .matchtime import fmt as fmt_time
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
        self.preview = None  # an animation the control panel asked to see
        # "gfx" runs the HUD; "observer" only shows another PC's HUD over the
        # game here, so it stops reading the game. None until one is picked.
        self.role = None
        # Player webcams: connections to player cams, and which player each
        # team's cam shows. The director reads this PC's screen for that.
        self.cams = CamHub()
        self.director = Director(config, self._director_roster, self._director_send)
        self.director.enabled = lambda: (bool(self.config.get()["cams"].get("director"))
                                         and self.role in ("gfx", "observer"))
        self._gfx_roster = (0.0, None)
        # Match time: every PC's League client at the same moment (see matchtime.py).
        self.match_sync = MatchSync(ReplayClient(cfg["api_url"]))
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

    def _gfx_base(self):
        from .overlay_window import gfx_base_url
        return gfx_base_url(self.config.get().get("observer_url") or "")

    def _director_roster(self):
        """Who's playing: from this PC's game, or the GFX PC's on an observer."""
        if self.role != "observer":
            with self._lock:
                return self.state["detail"].get("roster")
        at, roster = self._gfx_roster
        if time.time() - at > 2:
            try:
                roster = get_json(self._gfx_base() + "/api/state")["detail"].get("roster")
            except Exception:
                roster = None
            self._gfx_roster = (time.time(), roster)
        return roster

    def _director_send(self, focus, seen):
        if self.role == "observer":
            try:
                post_json(self._gfx_base() + "/api/cams/focus", {"focus": focus, "seen": seen})
            except Exception as e:
                raise RuntimeError(f"Couldn't reach the GFX PC: {getattr(e, 'reason', e)}")
        else:
            self.cams.report(focus, seen)

    def director_test(self):
        """One read of the name tags, with a picture of what was found."""
        import io
        try:
            focus, seen, image, lines = self.director.read_once()
        except Exception as e:
            return {"ok": False, "error": str(e) or repr(e)}
        from PIL import ImageDraw
        shot = image.convert("RGB")
        draw = ImageDraw.Draw(shot)
        for _, (x, y, w, h) in lines:
            draw.rectangle([x - 2, y - 2, x + w + 2, y + h + 2], outline=(255, 210, 0), width=2)
        for p in seen:
            colour = (49, 195, 146) if p["side"] == "left" else (227, 71, 90)
            draw.ellipse([p["x"] - 14, p["y"] - 14, p["x"] + 14, p["y"] + 14], outline=colour, width=4)
        shot.thumbnail((960, 540))
        buf = io.BytesIO()
        shot.save(buf, "JPEG", quality=80)
        return {"ok": True, "focus": focus, "seen": seen, "lines": [t for t, _ in lines][:40],
                "image": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}

    def sync_all(self):
        """Every client (this PC, the GFX PC and its observers) to 0:00."""
        if self.role == "observer":
            try:
                reply = post_json(self._gfx_base() + "/api/sync", {"time": 0, "scope": "all"})
            except Exception as e:
                raise ValueError(f"Couldn't reach the GFX PC: {getattr(e, 'reason', e)}")
            self.match_sync.status = "Sent to the GFX PC: all clients to 0:00"
            return reply
        return self.match_sync.issue(0, "all")

    def sync_gfx_to_me(self):
        """Observer: the GFX PC's game to this PC's time, half a second ahead."""
        if self.role != "observer":
            raise ValueError("This is for the Observer Client")
        mine = self.match_sync.mine_plus(0.5)
        try:
            reply = post_json(self._gfx_base() + "/api/sync", {"time": mine, "scope": "gfx"})
        except Exception as e:
            raise ValueError(f"Couldn't reach the GFX PC: {getattr(e, 'reason', e)}")
        if not reply.get("ok"):
            raise ValueError(reply.get("status") or reply.get("error") or "The GFX PC couldn't sync")
        self.match_sync.status = f"GFX PC synced to {fmt_time(mine)} (0.5 s ahead of this PC)"
        log(self.match_sync.status)
        return reply

    def swap_teams(self):
        cfg = self.config.get()
        pin = cfg["cams"].get("pin") or {}
        self.config.update({"left": cfg["right"], "right": cfg["left"],
                            "cams": {"pin": {"left": pin.get("right", 0), "right": pin.get("left", 0)}}})
        self.cams.swap_sides()
        log("Swapped teams left/right")

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
        self.director.stop()
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
        https = self.server.https_port if self.server and self.server.httpsd else None
        cam_host = lan or "localhost"
        return {"mode": self.mode, "role": self.role,
                "cam_url": f"https://{cam_host}:{https}/cam" if https else None, "demo_speed": self.demo_speed, "network": network, "port": port,
                "screen_gold": screen, "update": self.updater.info(),
                "overlay_window": self.overlay_window.info() if self.overlay_window else {"available": False},
                "director": self.director.status,
                "match_time": {"status": self.match_sync.status, "local": self.match_sync.local},
                "lan_url": f"http://{lan}:{port}" if lan else None, "data_dir": DATA_DIR}

    def snapshot(self):
        cfg = self.config.get()
        with self._lock:
            detail = self.state["detail"]
            return {"fields": self.state["fields"], "detail": detail,
                    "cams": self.cams.info(cfg, detail),
                    "updated_at": self.updated_at, "config": cfg,
                    "profiles_version": profiles.version(), "preview": self.preview,
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
            elif path == "/api/sync":
                self._send(json.dumps(app.match_sync.public()))
            elif path == "/api/observer/check":
                # Observer client: can this PC reach the GFX PC's HUD?
                if not _is_local(self.client_address[0]):
                    return self._send('{"error":"forbidden"}', status=403)
                self._send(json.dumps(check_gfx((parse_qs(url.query).get("url") or [""])[0])))
            elif path in ("/overlay", "/control", "/start", "/observer", "/cam"):
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

        def do_OPTIONS(self):
            # Player cams post here from their own PC's page.
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _cam_post(self, path, body):
            """Webcam messages: also taken from other PCs on the network."""
            if path == "/api/cam/poll":
                result = app.cams.poll(str(body.get("id") or ""), body.get("seat"), bool(body.get("claim")),
                                       body.get("label"), body.get("wait", 20))
                cfg = app.config.get()
                with app._lock:
                    roster = app.state["detail"].get("roster")
                result["teams"] = {s: {"name": cfg[s]["name"], "tricode": cfg[s]["tricode"],
                                       "players": [p["name"] for p in (roster or {}).get(s, [])]} for s in ("left", "right")}
                return result
            if path == "/api/cam/send":
                app.cams.send(str(body.get("to") or ""), str(body.get("from") or ""), body.get("data"))
                return {"ok": True}
            if path == "/api/cams/focus":
                focus = body.get("focus") or {}
                app.cams.report({s: focus.get(s) if focus.get(s) in (1, 2, 3, 4, 5) else None
                                 for s in ("left", "right")}, body.get("seen") or [])
                return {"ok": True}
            return None

        def do_POST(self):
            url = urlparse(self.path)
            path = url.path
            length = int(self.headers.get("Content-Length") or 0)
            if path == "/api/sync":
                # Observer clients: sync commands (all clients, or this PC's game).
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                    scope = "gfx" if body.get("scope") == "gfx" else "all"
                    return self._send(json.dumps(app.match_sync.issue(float(body.get("time") or 0), scope)))
                except (ValueError, TypeError) as e:
                    return self._send(json.dumps({"error": str(e)}), status=400)
            if path in ("/api/cam/poll", "/api/cam/send", "/api/cams/focus"):
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                    return self._send(json.dumps(self._cam_post(path, body)))
                except (ValueError, TypeError, AttributeError) as e:
                    return self._send(json.dumps({"error": str(e)}), status=400)
            # Other PCs may read the HUD (when allowed), but only this PC can
            # change it.
            if not _is_local(self.client_address[0]):
                return self._send('{"error":"Changes can only be made on the HUD PC"}', status=403)
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
                elif path == "/api/animation/preview":
                    if body.get("name") not in ("first_blood",) or body.get("side") not in ("left", "right"):
                        raise ValueError("Unknown animation")
                    app.preview = {"name": body["name"], "side": body["side"], "id": time.time()}
                elif path == "/api/role":
                    app.set_role(body.get("role"))
                elif path == "/api/match/sync-all":
                    app.sync_all()
                elif path == "/api/match/sync-gfx":
                    app.sync_gfx_to_me()
                elif path == "/api/match/enable":
                    changed = enable_replay_api()
                    log(f"Time control turned on in {changed}: restart the League client" if changed
                        else "Time control was already on in League's game.cfg")
                    app.match_sync.status = ("Turned on: restart the League spectator client" if changed
                                             else "Already on in League's game.cfg")
                elif path == "/api/teams/swap":
                    app.swap_teams()
                elif path == "/api/cams/test":
                    return self._send(json.dumps(app.director_test()))
                elif path == "/api/screen-gold/test":
                    return self._send(json.dumps(app.screen_gold.test()))
                else:
                    return self._send('{"error":"not found"}', status=404)
                app.refresh(fetch=False)
                self._send(json.dumps(app.snapshot()))
            except (ValueError, KeyError, OSError) as e:
                self._send(json.dumps({"error": str(e)}), status=400)

    return Handler


class TlsServer(ThreadingHTTPServer):
    """The same server over https (for the player cam page). The handshake
    happens in each connection's own thread, so a slow one holds nothing up."""

    daemon_threads = True

    def __init__(self, address, handler, context):
        self.context = context
        super().__init__(address, handler)

    def finish_request(self, request, client_address):
        try:
            request = self.context.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError):
            return  # e.g. a browser turning down the certificate before "Continue"
        super().finish_request(request, client_address)


class Server:
    """The HTTP server (and its https twin, for the player cam page) in
    background threads; can be restarted on a new host."""

    def __init__(self, app, host, port, https_port=None):
        self.app, self.port, self.https_port = app, port, https_port
        self.httpd = self.httpsd = None
        self._start(host)

    def _start(self, host):
        self.httpd = ThreadingHTTPServer((host, self.port), make_handler(self.app))
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.host = host
        self.httpsd = None
        if self.https_port:
            try:
                from .tls import server_context
                context = server_context(os.path.join(DATA_DIR, "certs"), ["localhost", "127.0.0.1", lan_ip()])
                if context:
                    self.httpsd = TlsServer((host, self.https_port), make_handler(self.app), context)
                    threading.Thread(target=self.httpsd.serve_forever, daemon=True).start()
            except Exception as e:  # the HUD works without it; only browser cams need it
                log(f"Couldn't start https on port {self.https_port} ({e}); browser cams won't work", "warn")

    def restart(self, host):
        if host == self.host:
            return
        self.stop()
        self._start(host)

    def stop(self):
        for server in (self.httpd, self.httpsd):
            if server:
                server.shutdown()
                server.server_close()


def start(app):
    """Start the web server and the game poller. Raises OSError if the port is taken."""
    cfg = app.config.get()
    port = cfg["http_port"]
    app.server = Server(app, app.host(), port, cfg.get("https_port") or port + 2)
    app.screen_gold.start()
    app.director.start()
    clean_up_old_update()
    app.updater.check_in_background()
    threading.Thread(target=app.run_poller, daemon=True).start()
    threading.Thread(target=run_follower, args=(app, app._stop), daemon=True).start()
    log(f"League HUD {app.updater.current} running - control panel http://localhost:{port}/control")
    log(f"Overlay (browser source, 1920x1080): http://localhost:{port}/overlay")
    log(f"vMix data source: http://localhost:{port}/hud.csv  - file: {app.config.output_dir()}")
    return app.server
