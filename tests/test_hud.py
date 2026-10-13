import copy
import csv
import io
import json
import os
import tempfile
import unittest

from lolhud import outputs
from lolhud.config import DEFAULTS, Config
from lolhud.gold import format_gold
from lolhud.source import DemoSource
from lolhud.state import build_state

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "allgamedata.json")


def load():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def cfg(**changes):
    c = copy.deepcopy(DEFAULTS)
    c.update(changes)
    return c


class StateTests(unittest.TestCase):
    def test_top_bar_numbers(self):
        f = build_state(load(), cfg())["fields"]
        self.assertEqual(f["TIMER"], "16:14")
        self.assertEqual(f["KILLS LEFT"], 6)
        self.assertEqual(f["KILLS RIGHT"], 3)
        # Blue lost a turret (to red) and red lost one to a blue minion.
        self.assertEqual(f["TURRETS LEFT"], 1)
        self.assertEqual(f["TURRETS RIGHT"], 1)
        self.assertEqual(f["VOID GRUB LEFT"], 2)
        self.assertEqual(f["VOID GRUB RIGHT"], 1)
        self.assertEqual(f["DRAGON TYPES LEFT"], "Infernal")
        self.assertEqual(f["DRAGON TYPES RIGHT"], "Ocean")
        self.assertEqual(f["HERALDS RIGHT"], 1)

    def test_swap_sides_puts_red_on_left(self):
        f = build_state(load(), cfg(swap_sides=True))["fields"]
        self.assertEqual(f["KILLS LEFT"], 3)
        self.assertEqual(f["VOID GRUB LEFT"], 1)

    def test_players_sorted_by_role(self):
        f = build_state(load(), cfg())["fields"]
        self.assertEqual(f["P1 CHAMPION"], "Gnar")
        self.assertEqual(f["P1 DEAD"], 1)
        self.assertEqual(f["P1 RESPAWN"], 12)
        self.assertEqual(f["P2 CHAMPION"], "Wukong")
        self.assertEqual(f["P6 CHAMPION"], "K'Sante")

    def test_timers(self):
        f = build_state(load(), cfg())["fields"]
        # Last dragon at 12:40 -> next at 17:40, 1:26 after 16:14.
        self.assertEqual(f["NEXT DRAGON"], "01:26")
        self.assertEqual(f["NEXT BARON"], "03:46")

    def test_grubs_and_herald_timers(self):
        raw = load()
        raw["gameData"]["gameTime"] = 300.0
        raw["events"]["Events"] = raw["events"]["Events"][:2]
        f = build_state(raw, cfg())["fields"]
        self.assertEqual(f["NEXT GRUBS"], "03:00")
        self.assertEqual(f["NEXT HERALD"], "10:00")
        self.assertEqual(f["NEXT ELDER"], "")
        # In the full fixture all three grubs and the herald are dead.
        f = build_state(load(), cfg())["fields"]
        self.assertEqual(f["NEXT GRUBS"], "")
        self.assertEqual(f["NEXT HERALD"], "")

    def test_elder_takes_over_after_soul(self):
        raw = load()
        for i, t in enumerate((300, 600, 900, 950)):
            raw["events"]["Events"].append({"EventName": "DragonKill", "EventTime": t,
                                            "DragonType": "Fire", "KillerName": "DSG Aelune"})
        raw["events"]["Events"].sort(key=lambda e: e["EventTime"])
        f = build_state(raw, cfg())["fields"]
        self.assertEqual(f["NEXT DRAGON"], "")
        self.assertEqual(f["NEXT ELDER"], "05:36")  # 950 + 360 - 974.5

    def test_timers_can_be_switched_off(self):
        f = build_state(load(), cfg(objective_timers={"baron": False}))["fields"]
        self.assertEqual(f["NEXT BARON"], "")
        self.assertEqual(f["NEXT DRAGON"], "01:26")

    def test_unknown_events_reported(self):
        detail = build_state(load(), cfg())["detail"]
        self.assertEqual(detail["unknown_events"], ["SomeFutureEvent"])

    def test_gold(self):
        f = build_state(load(), cfg())["fields"]
        self.assertGreater(f["GOLD RAW LEFT"], f["GOLD RAW RIGHT"])
        self.assertTrue(f["GOLD DIFF LEFT"].startswith("+"))
        self.assertEqual(f["GOLD DIFF RIGHT"], "")
        manual = build_state(load(), cfg(gold_mode="manual", manual_gold={"left": 31012, "right": 28100}))["fields"]
        self.assertEqual(manual["GOLD LEFT"], "31.0K")
        self.assertEqual(manual["GOLD DIFF LEFT"], "+2.9K")

    def test_series_pips(self):
        c = cfg()
        c["left"] = dict(c["left"], wins=1)
        f = build_state(load(), c)["fields"]
        self.assertEqual(f["BO3 1 LEFT WIN"], 1)
        self.assertEqual(f["BO3 2 LEFT WIN"], 0)
        self.assertTrue(f["BO3 1 LEFT"].endswith("pip_left_win.png"))

    def test_waiting_when_no_game(self):
        f = build_state(None, cfg())["fields"]
        self.assertEqual(f["STATUS"], "WAITING")
        self.assertEqual(f["TIMER"], "00:00")


class OutputTests(unittest.TestCase):
    def test_csv_round_trip(self):
        f = build_state(load(), cfg())["fields"]
        rows = list(csv.reader(io.StringIO(outputs.to_csv(f))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(dict(zip(*rows))["KILLS LEFT"], "6")

    def test_xml_tags_are_valid(self):
        xml = outputs.to_xml({"TRI-CODE LEFT": "A&B", "P1 NAME": "<x>"})
        self.assertIn("<TRI_CODE_LEFT>A&amp;B</TRI_CODE_LEFT>", xml)
        self.assertIn("<P1_NAME>&lt;x&gt;</P1_NAME>", xml)

    def test_file_writer(self):
        with tempfile.TemporaryDirectory() as d:
            outputs.FileWriter(d).write({"A": 1})
            self.assertEqual(sorted(os.listdir(d)), ["hud.csv", "hud.json", "hud.xml"])

    def test_format_gold(self):
        self.assertEqual(format_gold(31012), "31.0K")
        self.assertEqual(format_gold(31012, 2, ","), "31,01K")


class ConfigTests(unittest.TestCase):
    def test_update_merges_and_saves(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            c = Config(path)
            c.update({"left": {"tricode": "DSG"}})
            again = Config(path).get()
            self.assertEqual(again["left"]["tricode"], "DSG")
            self.assertEqual(again["left"]["wins"], 0)


class DemoTests(unittest.TestCase):
    def test_demo_estimate_is_close(self):
        demo = DemoSource(speed=0, start_at=1500, seed=4)
        raw = demo.fetch()
        f = build_state(raw, cfg())["fields"]
        for side, team in (("LEFT", "ORDER"), ("RIGHT", "CHAOS")):
            truth = demo.true_gold[team]
            self.assertLess(abs(f[f"GOLD RAW {side}"] - truth) / truth, 0.15)


if __name__ == "__main__":
    unittest.main()
