import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request

from lolhud.draft import DraftFeed, convert_session, find_client, play_recording

DEMO = os.path.join(os.path.dirname(__file__), "..", "web", "draft", "demo-draft.json")


def demo_points():
    with open(DEMO, encoding="utf-8") as f:
        return [p for p in json.load(f)["dataPoints"] if p.get("session")]


class ConvertTests(unittest.TestCase):
    def test_session_to_draft(self):
        points = demo_points()
        first = convert_session(points[5]["session"])
        self.assertEqual(first["phase"], "BAN PHASE 1")
        self.assertEqual([b["champion"] for b in first["blue"]["bans"]][:2], [266, 119])
        self.assertEqual(len(first["blue"]["picks"]), 5)
        last = convert_session(points[-1]["session"])
        self.assertEqual(len(last["blue"]["bans"]), 5)
        self.assertEqual(len(last["red"]["bans"]), 5)
        self.assertTrue(all(p["champion"] for p in last["blue"]["picks"] + last["red"]["picks"]))
        self.assertFalse(last["blue"]["active"] or last["red"]["active"])

    def test_the_team_picking_is_active(self):
        for p in demo_points():
            d = convert_session(p["session"])
            if d["phase"].startswith("PICK"):
                actives = [x for side in ("blue", "red") for x in d[side]["picks"] if x["active"]]
                self.assertEqual(len(actives), 1)
                return
        self.fail("no pick phase in the demo")


class DelayTests(unittest.TestCase):
    def test_shown_as_it_was_delay_ago(self):
        feed = DraftFeed()
        now = time.time()
        a = {"phase": "BAN PHASE 1", "timer_left": 30, "blue": {"bans": [1]}, "red": {}, "active": True}
        b = {"phase": "BAN PHASE 2", "timer_left": 30, "blue": {"bans": [1, 2]}, "red": {}, "active": True}
        feed.push(a, at=now - 20)
        feed.push(b, at=now - 8)
        self.assertEqual(feed.view(10)["phase"], "BAN PHASE 1")
        self.assertEqual(feed.view(10)["timer"], 20)          # its timer as it was then
        self.assertEqual(feed.view(0)["phase"], "BAN PHASE 2")
        self.assertFalse(feed.view(30)["active"])             # nothing yet, that far back
        feed.push(None, at=now - 1)                           # champ select over
        self.assertFalse(feed.view(0)["active"])
        self.assertEqual(feed.view(5)["phase"], "BAN PHASE 2")

    def test_time_until_bans(self):
        feed = DraftFeed()
        self.assertIsNone(feed.starts_in(150))
        now = time.time()
        feed.push({"phase": "BAN PHASE 1", "timer_left": 30, "blue": {}, "red": {}}, at=now - 40)
        feed.push({"phase": "BAN PHASE 1", "timer_left": 20, "blue": {"bans": [1]}, "red": {}}, at=now - 30)
        self.assertAlmostEqual(feed.starts_in(150), 110, delta=1)   # from when champ select began
        self.assertEqual(feed.starts_in(20), 0)                       # already on air
        feed.push(None)
        self.assertIsNone(feed.starts_in(150))

    def test_view_is_a_copy(self):
        feed = DraftFeed()
        feed.push({"phase": "X", "timer_left": 5, "blue": {"picks": [{"champion": 1}]}, "red": {}})
        v = feed.view(0)
        v["blue"]["picks"][0]["champion"] = {"name": "changed"}
        self.assertEqual(feed.view(0)["blue"]["picks"][0]["champion"], 1)

    def test_recording_replays_at_its_pace(self):
        feed, stop = DraftFeed(), threading.Event()
        t = threading.Thread(target=play_recording, args=(DEMO, feed.push, stop, 60.0))
        t.start()
        time.sleep(2.5)
        stop.set()
        t.join(5)
        drafts = [d for _, d in feed.timeline if d]
        self.assertGreater(len(drafts), 5)
        phases = [d["phase"] for d in drafts]
        self.assertEqual(phases[0], "BAN PHASE 1")
        self.assertIn("PICK PHASE 1", phases)


class ClientTests(unittest.TestCase):
    def test_lockfile(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "lockfile")
            with open(path, "w") as f:
                f.write("LeagueClient:1234:50123:s3cret:https")
            self.assertEqual(find_client([path]), (50123, "s3cret"))


class ApiTests(unittest.TestCase):
    def test_ingest_and_overlay_state(self):
        from lolhud.app import HudApp, start
        from lolhud.config import Config
        from lolhud.source import DemoSource
        with tempfile.TemporaryDirectory() as d:
            config = Config(os.path.join(d, "config.json"))
            config.update({"http_port": 8871, "https_port": 8873, "output_dir": os.path.join(d, "out"),
                           "draft": {"delay_on": False, "coach_left": "Coach L"}})
            app = HudApp(config, DemoSource(), mode="demo")
            app.updater.check_in_background = lambda: None
            app.ddragon.champions = {266: {"id": "Aatrox", "name": "Aatrox"}, 96: {"id": "KogMaw", "name": "Kog'Maw"}}
            start(app)
            try:
                draft = convert_session(demo_points()[20]["session"])
                req = urllib.request.Request("http://127.0.0.1:8871/api/draft/ingest", json.dumps({"draft": draft}).encode(),
                                             {"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=5).read()
                with urllib.request.urlopen("http://127.0.0.1:8871/api/draft", timeout=5) as r:
                    s = json.loads(r.read())
                self.assertTrue(s["draft"]["active"])
                self.assertEqual(s["draft"]["blue"]["bans"][0]["champion"]["name"], "Aatrox")
                self.assertIn("/img/champion/loading/KogMaw_0.jpg", s["draft"]["blue"]["picks"][0]["champion"]["loading"])
                self.assertEqual(s["teams"]["blue"]["coach"], "Coach L")
                self.assertEqual(s["logo"], "assets/logos/scc.png")          # no tournament logo yet
                self.assertEqual(s["teams"]["blue"]["logo_adjust"], {"scale": 1, "x": 0, "y": 0})
                self.assertEqual(s["teams"]["blue"]["name"], config.get()["left"]["name"])
                self.assertEqual(s["fonts"]["kill"]["family"], "Inter")                # the HUD's fonts
                self.assertEqual(s["fonts"]["short"]["family"], "Heathergreen")
                self.assertTrue(s["show"]["champ_names"])
                with urllib.request.urlopen("http://127.0.0.1:8871/draft", timeout=5) as r:
                    self.assertIn(b"europe.css", r.read())
                self.assertEqual(app.app_info()["draft"]["source"], "observer")
            finally:
                app.stop()
