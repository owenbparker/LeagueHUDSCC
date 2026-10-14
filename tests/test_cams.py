import copy
import json
import os
import threading
import time
import unittest

from lolhud.cams import CamHub, find_players, normalise, pick_focus
from lolhud.config import DEFAULTS
from lolhud.state import build_state

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "allgamedata.json")


def detail(**changes):
    with open(FIXTURE, encoding="utf-8") as f:
        raw = json.load(f)
    cfg = copy.deepcopy(DEFAULTS)
    cfg.update(changes)
    return build_state(raw, cfg)["detail"]


class RosterTests(unittest.TestCase):
    def test_seats_follow_positions(self):
        roster = detail()["roster"]
        self.assertEqual([p["name"] for p in roster["left"]],
                         ["DSG Atlas", "DSG Aelune", "DSG Wren", "DSG Ember", "DSG Quill"])
        self.assertEqual(roster["right"][2]["name"], "MU Moth")
        self.assertEqual(roster["left"][1]["champion_name"], "Wukong")

    def test_latest_kill(self):
        # DSG Wren (left mid) killed MU Moth (right mid).
        self.assertEqual(detail()["fight"], {"time": 300.1, "left": 3, "right": 3})
        swapped = detail(swap_sides=True)["fight"]
        self.assertEqual((swapped["left"], swapped["right"]), (3, 3))


class NameTagTests(unittest.TestCase):
    roster = {"left": [{"slot": 1, "names": ["DSG Atlas"], "champion_name": "Gnar"},
                       {"slot": 3, "names": ["DSG Wren"], "champion_name": "Ahri"}],
              "right": [{"slot": 2, "names": ["MU Cinder"], "champion_name": "Vi"},
                        {"slot": 5, "names": ["MU Brightvale"], "champion_name": "Rakan", "dead": True}]}

    def test_normalise(self):
        self.assertEqual(normalise("KC Yike"), "kcyike")
        self.assertEqual(normalise("Ñoño 01"), "nonool")

    def test_finds_name_tags(self):
        lines = [("DSG Wren", (900, 400, 80, 14)), ("MU Cinder", (1300, 300, 90, 14)),
                 ("DSG Atlas", (200, 700, 80, 14)), ("12", (10, 10, 10, 10))]
        seen = find_players(lines, self.roster)
        self.assertEqual({(p["side"], p["slot"]) for p in seen}, {("left", 3), ("right", 2), ("left", 1)})
        focus = pick_focus(seen, (960, 540))
        self.assertEqual(focus, {"left": 3, "right": 2})   # Wren is nearer the middle than Atlas

    def test_misread_letter_still_matches(self):
        seen = find_players([("DSG Wrcn", (900, 400, 80, 14)), ("MU Cinbcr", (0, 0, 1, 1))], self.roster)
        self.assertEqual([(p["side"], p["slot"]) for p in seen], [("left", 3)])  # one letter off, not two

    def test_dead_players_and_short_names_ignored(self):
        seen = find_players([("MU Brightvale", (900, 400, 80, 14)), ("Vi", (900, 500, 20, 14))], self.roster)
        self.assertEqual(seen, [])
        self.assertEqual(pick_focus([], (960, 540)), {"left": None, "right": None})


class HubTests(unittest.TestCase):
    def test_messages_and_seats(self):
        hub = CamHub()
        hub.poll("cam1", seat="left-2", claim=True, label="Logitech", wait=0)
        hub.poll("viewer", wait=0)
        self.assertEqual(hub.publishers(), {"left-2": {"id": "cam1", "label": "Logitech"}})
        got = {}
        t = threading.Thread(target=lambda: got.update(hub.poll("cam1", seat="left-2", wait=5)))
        t.start()
        time.sleep(0.1)
        hub.send("cam1", "viewer", {"type": "offer", "sdp": "x"})
        t.join(2)
        self.assertEqual(got["messages"], [{"type": "offer", "sdp": "x", "from": "viewer"}])
        with self.assertRaises(ValueError):
            hub.send("nobody", "viewer", {"type": "offer"})
        # A second cam claiming the seat takes it.
        hub.poll("cam2", seat="left-2", claim=True, wait=0)
        self.assertEqual(hub.publishers()["left-2"]["id"], "cam2")
        self.assertEqual(hub.poll("cam1", seat="left-2", wait=0)["messages"][0]["type"], "replaced")
        # Swapping teams moves the cam; its next check-in doesn't undo that.
        hub.swap_sides()
        self.assertEqual(hub.poll("cam2", seat="left-2", wait=0)["seat"], "right-2")
        self.assertIn("right-2", hub.publishers())

    def test_who_is_on_camera(self):
        hub = CamHub()
        cfg = copy.deepcopy(DEFAULTS)
        d = {"game_time": 305, "fight": {"time": 300.1, "left": 3, "right": 4}}
        self.assertEqual(hub.resolve(cfg, d)["left"], 3)          # the latest kill
        hub.report({"left": 1, "right": 2})
        f = hub.resolve(cfg, d)
        self.assertEqual((f["left"], f["right"]), (3, 4))          # held: switched less than 3 s ago
        cfg["cams"]["hold"] = 0
        f = hub.resolve(cfg, d)
        self.assertEqual((f["left"], f["right"], f["why"]["left"]), (1, 2, "on screen"))
        cfg["cams"]["pin"]["right"] = 5
        self.assertEqual(hub.resolve(cfg, d)["right"], 5)
        cfg["cams"]["auto"] = False
        cfg["cams"]["pin"]["right"] = 0
        hub.report({"left": 4, "right": 4})
        self.assertEqual(hub.resolve(cfg, d)["left"], 1)          # auto off: stays put


if __name__ == "__main__":
    unittest.main()


class DirectorTests(unittest.TestCase):
    def test_reads_tags_where_they_are_on_screen(self):
        from PIL import Image
        from lolhud import cams, ocr
        from lolhud.config import Config
        import tempfile

        class FakeOcr:
            def read_lines(self, image):
                # The crop is the name-tag area at 2x: report a tag at screen
                # (1000, 500) and one at (300, 600), in crop pixels.
                self.size = image.size
                k = 2590 / 1680   # scaled up, to the most the text recognition takes
                to_crop = lambda x, y: ((x - 120) * k, (y - 110) * k)
                return [("MU Cinder", (*to_crop(1000, 500), 60 * k, 14 * k)),
                        ("DSG Wren", (*to_crop(300, 600), 60 * k, 14 * k))]

        with tempfile.TemporaryDirectory() as d:
            config = Config(os.path.join(d, "config.json"))
            roster = detail()["roster"]
            director = cams.Director(config, lambda: roster, lambda f, s: None)
            director._ocr = FakeOcr()
            real = ocr.capture_league
            ocr.capture_league = lambda full=False: ((Image.new("RGB", (1920, 1080)), 1080), None)
            try:
                focus, seen, _, _ = director.read_once()
            finally:
                ocr.capture_league = real
        self.assertEqual(director._ocr.size, (2590, round(690 * 2590 / 1680)))
        self.assertEqual(focus, {"left": 3, "right": 2})
        tag = next(p for p in seen if p["side"] == "right")
        self.assertEqual((tag["x"], tag["y"]), (1030, 507))   # centre of the tag, in screen pixels
