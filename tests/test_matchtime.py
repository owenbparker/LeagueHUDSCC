import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from lolhud.matchtime import enable_replay_api, playback_url


def fake_league(port, start_time):
    """A League client's Replay API: /replay/playback, playing at 1x."""
    game = {"time": float(start_time), "at": time.time(), "seeks": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _reply(self):
            now = game["time"] + (time.time() - game["at"])
            body = json.dumps({"time": now, "paused": False, "speed": 1.0}).encode()
            self.send_response(200 if self.path == "/replay/playback" else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._reply()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if "time" in body:
                game["time"], game["at"] = float(body["time"]), time.time()
                game["seeks"].append(float(body["time"]))
            self._reply()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, game


class MatchTimeTests(unittest.TestCase):
    def test_playback_url(self):
        self.assertEqual(playback_url("https://127.0.0.1:2999/liveclientdata/allgamedata"),
                         "https://127.0.0.1:2999/replay/playback")

    def test_enable_replay_api(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "game.cfg")
            with open(cfg, "w") as f:
                f.write("[General]\nWindowMode=2\n\n[HUD]\nShowTimestamps=0\n")
            self.assertEqual(enable_replay_api(cfg), cfg)
            with open(cfg) as f:
                text = f.read()
            self.assertIn("[General]\nEnableReplayApi=1\nWindowMode=2", text)
            self.assertIsNone(enable_replay_api(cfg))   # already on

    def test_sync_all_and_sync_gfx(self):
        from lolhud.app import HudApp, start
        from lolhud.config import Config
        gfx_league, gfx_game = fake_league(8841, 263)
        obs_league, obs_game = fake_league(8842, 250)
        with tempfile.TemporaryDirectory() as d:
            apps = []
            for name, port, league in (("gfx", 8821, 8841), ("obs", 8831, 8842)):
                config = Config(os.path.join(d, name + ".json"))
                config.update({"http_port": port, "https_port": port + 2, "output_dir": os.path.join(d, name),
                               "api_url": f"http://127.0.0.1:{league}/liveclientdata/allgamedata",
                               "observer_url": "http://127.0.0.1:8821/overlay" if name == "obs" else ""})
                app = HudApp(config)
                app.updater.check_in_background = lambda: None
                start(app)
                apps.append(app)
            gfx, obs = apps
            gfx.set_role("gfx")
            obs.set_role("observer")
            try:
                time.sleep(0.8)   # the observer finds the GFX PC
                # From the observer: every client to 0:00.
                obs.sync_all()
                deadline = time.time() + 3
                while not obs_game["seeks"] and time.time() < deadline:
                    time.sleep(0.05)
                self.assertEqual(gfx_game["seeks"], [0.0])
                self.assertEqual(len(obs_game["seeks"]), 1)
                self.assertLess(obs_game["seeks"][0], 1.0)   # 0:00 plus the moment it took to arrive
                # The observer's game is at ~0:05; the GFX PC goes half a second ahead of it.
                obs_game["time"], obs_game["at"] = 5.0, time.time()
                obs.sync_gfx_to_me()
                self.assertAlmostEqual(gfx_game["seeks"][-1], 5.5, delta=0.2)
                time.sleep(0.6)
                self.assertEqual(len(obs_game["seeks"]), 1)   # "Sync GFX" leaves the observers alone
            finally:
                for app in apps:
                    app.stop()
                for server in (gfx_league, obs_league):
                    server.shutdown()
                    server.server_close()
