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
