"""SCC Player Cam: a small separate app for players' PCs that sends their
webcam to the HUD on the GFX PC (see web/cam.html and lolhud/cams.py).

    PlayerCam.exe              the built app (installed by SCC-Player-Cam-Setup.exe)
    python -m lolhud.camapp    the same, from source (needs: pip install pywebview)

The cam page needs a secure page to use the camera; it's served here on
http://127.0.0.1, which counts as one, and talks to the GFX PC over the
network. Players can instead open the GFX PC's https link in a browser.
"""

import argparse
import json
import mimetypes
import os
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .config import DATA_DIR, web_file
from .log import log
from .updater import Updater, clean_up_old_update

TITLE = "SCC Player Cam"
# Set once the window lets the page use the camera (the page waits for it).
STATUS = {"camera_ready": False}
_handlers = []  # .NET event handlers, kept alive
# Only these files are served (and bundled into the app).
FILES = {"/cam": "cam.html", "/update.js": "update.js", "/fonts/Inter.ttf": "fonts/Inter.ttf",
         "/assets/app_icon.png": "assets/app_icon.png"}


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
            rel = FILES.get(path)
            full = web_file(rel) if rel else None
            if not full:
                return self._send('{"error":"not found"}', status=404)
            with open(full, "rb") as f:
                self._send(f.read(), mimetypes.guess_type(full)[0] or "application/octet-stream")

        def do_POST(self):
            path = urlparse(self.path).path
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                if path == "/api/update/check":
                    updater.check()
                elif path == "/api/update/install":
                    updater.install_in_background()
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
