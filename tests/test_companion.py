import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request

from lolhud import companion


def call(port, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/companion", data, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


class SponsorTests(unittest.TestCase):
    def test_sponsor_upload_and_save(self):
        import base64
        from unittest import mock
        from lolhud import app as app_module, config as config_module
        from lolhud.app import HudApp, start
        from lolhud.config import Config
        from lolhud.source import DemoSource
        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, "sponsors")
            with mock.patch.object(app_module, "SPONSOR_DIR", folder), mock.patch.object(config_module, "SPONSOR_DIR", folder):
                config = Config(os.path.join(d, "config.json"))
                config.update({"http_port": 8861, "https_port": 8863, "output_dir": os.path.join(d, "out")})
                app = HudApp(config, DemoSource(), mode="demo")
                app.updater.check_in_background = lambda: None
                start(app)
                try:
                    png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
                    post = lambda path, body: json.loads(urllib.request.urlopen(urllib.request.Request(
                        f"http://127.0.0.1:8861{path}", json.dumps(body).encode(), {"Content-Type": "application/json"}),
                        timeout=5).read())
                    a = post("/api/sponsor", {"filename": "Acme Logo.png", "data": png})["path"]
                    b = post("/api/sponsor", {"filename": "Acme Logo.png", "data": png})["path"]
                    self.assertEqual((a, b), ("sponsors/Acme_Logo.png", "sponsors/Acme_Logo-2.png"))   # never overwrites
                    with urllib.request.urlopen(f"http://127.0.0.1:8861/{a}", timeout=5) as r:
                        self.assertTrue(r.read().startswith(b"\x89PNG"))
                    post("/api/sponsors", {"sponsors": [b, a, "sponsors/missing.png"], "seconds": 3})
                    box = config.get()["sponsor_box"]
                    self.assertEqual(box["sponsors"], [b, a])     # unknown files dropped, order kept
                    self.assertEqual(box["seconds"], 3)
                    self.assertTrue(box["enabled"])               # the rest of the box untouched
                    with self.assertRaises(urllib.error.HTTPError):
                        post("/api/sponsor", {"filename": "evil.exe", "data": png})
                finally:
                    app.stop()


class CompanionTests(unittest.TestCase):
    def test_module_lists_match_the_app(self):
        # companion-module/src/definitions.js names the same settings and commands.
        path = os.path.join(os.path.dirname(__file__), "..", "companion-module", "src", "definitions.js")
        with open(path, encoding="utf-8") as f:
            js = f.read()
        for key in list(companion.TOGGLES) + list(companion.STATES) + list(companion.VALUES) + list(companion.COMMANDS):
            self.assertIn(f"{key}:", js, key)

    def test_toggles_values_and_commands(self):
        from lolhud.app import HudApp, start
        from lolhud.config import Config
        from lolhud.source import DemoSource
        with tempfile.TemporaryDirectory() as d:
            config = Config(os.path.join(d, "config.json"))
            config.update({"http_port": 8851, "https_port": 8853, "output_dir": os.path.join(d, "out")})
            app = HudApp(config, DemoSource(), mode="demo")
            app.updater.check_in_background = lambda: None
            start(app)
            try:
                s = call(8851)
                self.assertTrue(s["toggles"]["timer_baron"])
                self.assertFalse(s["toggles"]["cams_director"])
                s = call(8851, {"action": "toggle", "id": "timer_baron"})
                self.assertFalse(s["toggles"]["timer_baron"])
                self.assertFalse(config.get()["objective_timers"]["baron"])
                self.assertTrue(config.get()["objective_timers"]["dragon"])   # the others untouched
                s = call(8851, {"action": "on", "id": "cams_director"})
                self.assertTrue(config.get()["cams"]["director"])
                self.assertTrue(config.get()["cams"]["enabled"])
                s = call(8851, {"action": "adjust", "id": "bg_speed", "value": -0.25})
                self.assertAlmostEqual(s["values"]["bg_speed"], 0.75)
                s = call(8851, {"action": "set", "id": "bg_brightness", "value": 9})
                self.assertAlmostEqual(s["values"]["bg_brightness"], 2.5)   # kept in range
                self.assertEqual(s["variables"]["bg_brightness"], "250%")
                s = call(8851, {"action": "adjust", "id": "timers_auto_count", "value": 5})
                self.assertEqual(config.get()["objective_timers_auto_count"], 3)
                self.assertEqual(s["variables"]["timers_auto_count"], "3")
                call(8851, {"action": "run", "id": "preview_fb_right"})
                self.assertEqual(app.preview["side"], "right")
                with self.assertRaises(urllib.error.HTTPError) as e:
                    call(8851, {"action": "run", "id": "sync_gfx"})   # observer only
                self.assertEqual(e.exception.code, 400)
                with self.assertRaises(urllib.error.HTTPError):
                    call(8851, {"action": "toggle", "id": "nope"})
            finally:
                app.stop()
