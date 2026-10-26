"""SCC Player Cam: a small separate app for players' PCs that sends their
webcam to the HUD on the GFX PC (see web/cam.html and lolhud/cams.py).

    PlayerCam.exe              the built app (installed by SCC-Player-Cam-Setup.exe)
    python -m lolhud.camapp    the same, from source (needs: pip install pywebview)

The cam page needs a secure page to use the camera; it's served here on
http://127.0.0.1, which counts as one, and talks to the GFX PC over the
network. Players can instead open the GFX PC's https link in a browser.
"""

import argparse
import base64
import json
import mimetypes
import os
import ssl
import subprocess
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import camprops
from .config import DATA_DIR, web_file
from .log import log
from .updater import Updater, clean_up_old_update

TITLE = "SCC Player Cam"
# Set once the window lets the page use the camera (the page waits for it).
STATUS = {"camera_ready": False}
_handlers = []  # .NET event handlers, kept alive
# Only these files are served (and bundled into the app).
FILES = {"/cam": "cam.html", "/update.js": "update.js", "/fonts/Inter.ttf": "fonts/Inter.ttf",
         "/assets/playercam_icon.png": "assets/playercam_icon.png",
         "/assets/games/lol.svg": "assets/games/lol.svg", "/assets/games/valorant.svg": "assets/games/valorant.svg"}


# --- the player's Riot account (VALORANT) -----------------------------------
#
# Their Riot ID is read from the Riot Client on this PC, which they're signed
# in to to play: its local API (port and password in its lockfile) answers
# with the signed-in account's name and tag. Nothing leaves this PC.

def riot_lockfile():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/AppData/Local")
    return os.path.join(base, "Riot Games", "Riot Client", "Config", "lockfile")


def _riot_get(port, password, path):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE   # the Riot Client's own certificate, on this PC
    auth = base64.b64encode(f"riot:{password}".encode()).decode()
    req = urllib.request.Request(f"https://127.0.0.1:{port}{path}", headers={"Authorization": f"Basic {auth}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))
    with opener.open(req, timeout=3) as r:
        return json.loads(r.read() or b"null")


def riot_account(lockfile=None):
    """{"riot_id": "Name#TAG"} for the account signed in to the Riot Client
    here, or {"error": why, "can_sign_in": bool}."""
    try:
        with open(lockfile or riot_lockfile(), encoding="utf-8") as f:
            parts = f.read().strip().split(":")   # name:pid:port:password:protocol
        port, password = int(parts[2]), parts[3]
    except (OSError, ValueError, IndexError):
        return {"error": "The Riot Client isn't running on this PC.", "can_sign_in": bool(riot_client_exe())}
    for path, name_key, tag_key in (("/player-account/aliases/v1/active", "game_name", "tag_line"),
                                    ("/chat/v1/session", "game_name", "game_tag")):
        try:
            data = _riot_get(port, password, path) or {}
            if isinstance(data, list):
                data = data[0] if data else {}
            if data.get(name_key) and data.get(tag_key):
                return {"riot_id": f"{data[name_key]}#{data[tag_key]}"}
        except Exception:
            continue
    return {"error": "Sign in to the Riot Client, then try again.", "can_sign_in": bool(riot_client_exe())}


def riot_client_exe():
    """RiotClientServices.exe, from where the Riot installer records it."""
    if sys.platform != "win32":
        return None
    try:
        with open(os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Riot Games", "RiotClientInstalls.json"),
                  encoding="utf-8") as f:
            installs = json.load(f)
        for key in ("rc_default", "rc_live"):
            if installs.get(key) and os.path.exists(installs[key]):
                return installs[key]
    except (OSError, ValueError):
        pass
    default = r"C:\Riot Games\Riot Client\RiotClientServices.exe"
    return default if os.path.exists(default) else None


def open_riot_client():
    """Open the Riot Client (its sign-in window when signed out)."""
    exe = riot_client_exe()
    if not exe:
        raise ValueError("The Riot Client isn't installed on this PC: type your Riot ID instead.")
    subprocess.Popen([exe], close_fds=True)


HUD_PORTS = {"lol": 8777, "val": 8778}


def find_huds_for(game):
    """[{"ip", "url", "teams"}] of the GFX PCs running the game's HUD (not
    observer PCs) on this PC's networks."""
    from .discover import find_huds
    key = "game" if game == "val" else "detail"   # (what each HUD's state carries)

    def is_gfx(state):
        return key in state and (state.get("app") or {}).get("role") != "observer"
    out = []
    for hit in find_huds(HUD_PORTS.get(game, 8777), is_gfx):
        cfg = hit["state"].get("config") or {}
        names = [(cfg.get(side) or {}).get("name") for side in ("left", "right")]
        out.append({"ip": hit["ip"], "url": hit["url"], "teams": " vs ".join(n for n in names if n)})
    return out


def _camprops(fn, *args):
    """{"props": ...}, with "error" when the driver couldn't be reached."""
    try:
        found = fn(*args)
        return {"props": found} if found or sys.platform != "win32" else \
            {"props": {}, "error": "the camera's driver lists no settings for it (or it wasn't found)"}
    except Exception as e:   # (no such camera, or its driver said no)
        log(f"Camera settings: {e!r}", "warn")
        return {"props": {}, "error": repr(e)}


def make_handler(updater):
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
            self.end_headers()
            self.wfile.write(body)

        def _state(self):
            return json.dumps({"app": {"update": updater.info(), **STATUS}})

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/state":
                return self._send(self._state())
            if path == "/api/riot/account":
                return self._send(json.dumps(riot_account()))
            if path == "/api/camprops":
                # The camera's driver settings the browser can't reach (gain...).
                label = (parse_qs(urlparse(self.path).query).get("label") or [""])[0]
                return self._send(json.dumps(_camprops(camprops.props, label)))
            if path == "/api/find":
                # The PCs on the network running this game's HUD.
                game = (parse_qs(urlparse(self.path).query).get("game") or ["lol"])[0]
                return self._send(json.dumps({"huds": find_huds_for(game)}))
            rel = FILES.get(path)
            full = web_file(rel) if rel else None
            if not full:
                return self._send('{"error":"not found"}', status=404)
            with open(full, "rb") as f:
                self._send(f.read(), mimetypes.guess_type(full)[0] or "application/octet-stream")

        def do_POST(self):
            path = urlparse(self.path).path
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                if path == "/api/camprops":
                    body = json.loads(raw or b"{}")
                    return self._send(json.dumps(_camprops(camprops.set_props, str(body.get("label") or ""),
                                                           body.get("set") or {})))
                if path == "/api/update/check":
                    updater.check()
                elif path == "/api/update/install":
                    updater.install_in_background()
                elif path == "/api/riot/sign-in":
                    open_riot_client()
                    return self._send(json.dumps({"ok": True}))
                else:
                    return self._send('{"error":"not found"}', status=404)
                self._send(self._state())
            except ValueError as e:
                self._send(json.dumps({"error": str(e)}), status=400)

    return Handler


def serve(updater, port=0):
    """Start the page server on 127.0.0.1 (a free port unless given)."""
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(updater))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def allow_camera(window):
    """Let the page in this window use the camera without asking.

    The window is Edge WebView2, which pywebview leaves to its default for
    camera requests; depending on the WebView2 version that can refuse them
    outright, leaving the page with no camera. The window only ever shows
    this app's own page, so camera (and microphone) requests are allowed."""
    try:
        from System import Action
        from Microsoft.Web.WebView2.Core import CoreWebView2PermissionKind, CoreWebView2PermissionState
        form = window.native

        def on_request(sender, args):
            if args.PermissionKind in (CoreWebView2PermissionKind.Camera, CoreWebView2PermissionKind.Microphone):
                args.State = CoreWebView2PermissionState.Allow

        def attach():
            form.webview.CoreWebView2.PermissionRequested += on_request

        _handlers.append(on_request)
        form.Invoke(Action(attach))  # WebView2 is only touched on its own thread
    except Exception as e:
        log(f"Couldn't set up camera access ({e!r})", "warn")
    finally:
        STATUS["camera_ready"] = True


def smoke_test(port):
    """Used by the build: the packaged app serves its page and has its window
    library. Writes smoke-test.txt next to the app."""
    results, ok = [], True
    for path in list(FILES) + ["/api/state"]:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
                results.append(f"OK   {path} ({len(r.read())} bytes)")
        except Exception as e:
            ok = False
            results.append(f"FAIL {path}: {e}")
    try:
        # Lists the cameras through their drivers (none on the build PC).
        found = camprops.props("")
        results.append(f"OK   camera settings ({len(found)})")
    except Exception as e:
        ok = False
        results.append(f"FAIL camera settings: {e!r}")
    try:
        import webview  # noqa: F401
        if sys.platform == "win32":
            import webview.platforms.winforms  # noqa: F401
        results.append("OK   window library")
    except Exception as e:
        ok = False
        results.append(f"FAIL window library: {e!r}")
    with open(os.path.join(DATA_DIR, "smoke-test.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(results + ["PASSED" if ok else "FAILED"]) + "\n")
    return ok


def main():
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    parser = argparse.ArgumentParser(description=TITLE)
    parser.add_argument("--debug", action="store_true", help="allow right-click > Inspect in the window")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    updater = Updater(asset="PlayerCam-windows.zip", exe="PlayerCam.exe", name=TITLE)
    httpd = serve(updater)
    port = httpd.server_address[1]
    if args.smoke_test:
        ok = smoke_test(port)
        httpd.shutdown()
        return 0 if ok else 1
    clean_up_old_update()
    updater.check_in_background()
    url = f"http://127.0.0.1:{port}/cam?app=1"
    # Belt and braces with allow_camera(): WebView2 answers camera prompts
    # with yes (the real camera; only the prompt is skipped).
    os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = "--use-fake-ui-for-media-stream"
    try:
        import webview
        window = webview.create_window(f"{TITLE} {updater.current}", url, width=960, height=680,
                                       min_size=(520, 560), background_color="#0e1012")
        updater.on_exit = window.destroy

        def loaded():
            if not STATUS["camera_ready"]:
                allow_camera(window)
        window.events.loaded += loaded
        # Not private: remembers the GFX address, seat, camera and the
        # camera permission between runs.
        webview.start(debug=args.debug, private_mode=False, storage_path=os.path.join(DATA_DIR, "webview"))
    except Exception as e:
        log(f"Couldn't open the app window ({e!r})", "warn")
        STATUS["camera_ready"] = True
        import webbrowser
        webbrowser.open(url)
        text = "Player Cam has opened in your web browser. It keeps running until you close this box."
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, TITLE, 0x40)
        else:
            input(text + " (Press Enter to stop.)\n")
    finally:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
