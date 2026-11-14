"""Start screen memory: last client + "Open this role next time" (startpick.py)."""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from lolhud import startpick


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class StartPickTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        data_dir = self.dir

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if not startpick.handle(self, "GET", urlparse(self.path).path, data_dir):
                    self.send_response(200)
                    self.send_header("Content-Length", "4")
                    self.end_headers()
                    self.wfile.write(b"page")

            def do_POST(self):
                startpick.handle(self, "POST", urlparse(self.path).path, data_dir)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
        self.open = urllib.request.build_opener(NoRedirect).open

    def tearDown(self):
        self.srv.shutdown()

    def post(self, body):
        req = urllib.request.Request(self.base + "/api/start", json.dumps(body).encode(), {"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req).read())

    def start(self, q=""):
        try:
            r = self.open(self.base + "/start" + q)
            return r.status, None
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Location")

    def test_defaults_show_the_screen(self):
        self.assertEqual(json.loads(urllib.request.urlopen(self.base + "/api/start").read()), {"last": None, "auto": False})
        self.assertEqual(self.start(), (200, None))

    def test_auto_opens_the_last_client(self):
        self.post({"last": "observer", "auto": True})
        self.assertEqual(self.start(), (302, "/observer"))
        self.post({"last": "gfx"})
        self.assertEqual(self.start(), (302, "/control"))
        self.assertTrue(os.path.exists(os.path.join(self.dir, "start.json")))

    def test_change_client_link_always_shows_it(self):
        self.post({"last": "gfx", "auto": True})
        self.assertEqual(self.start("?pick=1"), (200, None))

    def test_auto_without_a_last_client_shows_it(self):
        self.post({"auto": True})
        self.assertEqual(self.start(), (200, None))

    def test_bad_values_are_ignored(self):
        self.assertEqual(self.post({"last": "hacker", "auto": "yes", "x": 1}), {"last": None, "auto": False})


if __name__ == "__main__":
    unittest.main()
