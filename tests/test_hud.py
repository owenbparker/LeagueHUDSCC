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

    def test_first_blood(self):
        detail = build_state(load(), cfg())["detail"]
        self.assertEqual(detail["first_blood"], {"side": "left", "time": 300.1})   # DSG Wren (blue side)
        swapped = build_state(load(), cfg(swap_sides=True))["detail"]
        self.assertEqual(swapped["first_blood"]["side"], "right")
        raw = load()
        raw["events"]["Events"] = [e for e in raw["events"]["Events"] if e["EventName"] not in ("FirstBlood", "ChampionKill")]
        self.assertIsNone(build_state(raw, cfg())["detail"]["first_blood"])

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


class GoldTests(unittest.TestCase):
    def test_early_game_matches_2026_passive_gold(self):
        # Spectator top bar read 3.2k for a team at 1:46 with no CS or kills.
        raw = load()
        raw["gameData"]["gameTime"] = 106.0
        raw["events"]["Events"] = []
        for p in raw["allPlayers"]:
            p["scores"] = {"kills": 0, "deaths": 0, "assists": 0, "creepScore": 0}
            p["items"] = []
        f = build_state(raw, cfg())["fields"]
        self.assertEqual(f["GOLD LEFT"], "3.2K")


class ScreenGoldTests(unittest.TestCase):
    def test_screen_values_used_by_team(self):
        f = build_state(load(), cfg(), screen_gold={"blue": 8000, "red": 8900})["fields"]
        self.assertEqual((f["GOLD LEFT"], f["GOLD RIGHT"], f["GOLD DIFF RIGHT"]), ("8.0K", "8.9K", "+0.9K"))
        # Blue is still blue when it's shown on the right.
        f = build_state(load(), cfg(swap_sides=True), screen_gold={"blue": 8000, "red": 8900})["fields"]
        self.assertEqual(f["GOLD RIGHT"], "8.0K")

    def test_falls_back_to_estimate(self):
        state = build_state(load(), cfg(), screen_gold=None)
        self.assertEqual(state["detail"]["gold_source"], "estimate")
        self.assertGreater(state["fields"]["GOLD RAW LEFT"], 0)

    def test_screen_counters_override_events(self):
        counts = {"blue": {"dragons": 2, "heralds": 1, "grubs": 0}, "red": {"dragons": 0, "heralds": 0, "grubs": 3}}
        f = build_state(load(), cfg(), screen_counts=counts)["fields"]
        self.assertEqual((f["DRAGONS LEFT"], f["HERALDS LEFT"], f["VOID GRUB LEFT"]), (2, 1, 0))
        self.assertEqual((f["DRAGONS RIGHT"], f["HERALDS RIGHT"], f["VOID GRUB RIGHT"]), (0, 0, 3))
        # A counter that couldn't be read keeps the events' number.
        counts["red"]["grubs"] = None
        f = build_state(load(), cfg(), screen_counts=counts)["fields"]
        self.assertEqual(f["VOID GRUB RIGHT"], 1)

    def test_screen_towers_override_events(self):
        counts = {"blue": {"towers": 1}, "red": {"towers": 0}}
        state = build_state(load(), cfg(), screen_counts=counts)
        self.assertEqual((state["fields"]["TURRETS LEFT"], state["fields"]["TURRETS RIGHT"]), (1, 0))
        self.assertEqual(state["detail"]["tower_check"], {"blue": (1, 1), "red": (1, 0)})

    def test_repeated_events_count_once(self):
        raw = load()
        raw["events"]["Events"] += [dict(e) for e in raw["events"]["Events"] if e["EventName"] == "TurretKilled"]
        f = build_state(raw, cfg())["fields"]
        self.assertEqual((f["TURRETS LEFT"], f["TURRETS RIGHT"]), (1, 1))

    def test_screen_counters_end_timers(self):
        raw = load()
        raw["events"]["Events"] = raw["events"]["Events"][:2]   # no grub/herald events at all
        raw["gameData"]["gameTime"] = 950.0
        counts = {"blue": {"dragons": 0, "heralds": 1, "grubs": 0}, "red": {"dragons": 0, "heralds": 0, "grubs": 3}}
        f = build_state(raw, cfg(), screen_counts=counts)["fields"]
        self.assertEqual((f["NEXT GRUBS"], f["NEXT HERALD"]), ("", ""))

    def test_finds_counters_moved_up_by_bounties(self):
        from PIL import Image
        from lolhud.ocr import COUNTER_BOXES, DEFAULT_BOXES, locate, scaled_box
        here = os.path.dirname(__file__)
        image = Image.open(os.path.join(here, "fixtures", "spectator_topbar_bounties.png")).convert("RGB")

        def shift(team, box):
            placed = scaled_box(box, image.width, 1079)
            return locate(image, placed, team, 1079)[1] - placed[1]

        for name, box in COUNTER_BOXES["red"].items():
            self.assertTrue(-11 <= shift("red", box) <= -7, name)   # raised ~9px
        for name, box in COUNTER_BOXES["blue"].items():
            self.assertTrue(-2 <= shift("blue", box) <= 2, name)    # blue has no bounties
        self.assertTrue(-2 <= shift("red", DEFAULT_BOXES["red"]) <= 2)  # gold doesn't move

    def test_parse_count(self):
        from lolhud.ocr import parse_count
        for text, value in {"3": 3, "O": 0, "o": 0, "l": 1, "11": 11, "": None, "abc": None}.items():
            self.assertEqual(parse_count(text), value, text)

    def test_parse_gold(self):
        from lolhud.ocr import parse_gold
        cases = {"8.0k": 8000, "8,9k": 8900, "B.Ok": 8000, "12.4K": 12400, "100.2k": 100200,
                 "l2.5k": 12500, "3": None, "": None}
        for text, value in cases.items():
            self.assertEqual(parse_gold(text), value, text)

    def test_scaled_box(self):
        from lolhud.ocr import scaled_box
        self.assertEqual(scaled_box([804, 16, 54, 24], 1920, 1080), (804, 16, 858, 40))
        # 2560x1440: scaled by height, still centred.
        self.assertEqual(scaled_box([804, 16, 54, 24], 2560, 1440), (1072, 21, 1144, 53))

    def test_one_unreadable_counter_doesnt_drop_the_others(self):
        import time
        from lolhud.ocr import ScreenGold, COUNTERS
        sg = ScreenGold.__new__(ScreenGold)
        sg.counts = {t: {c: None for c in COUNTERS} for t in ("blue", "red")}
        sg.counts_at, sg._pending_counts = {}, {}
        sg._accept_count("red", "grubs", 3)
        sg._accept_count("red", "towers", None)   # this one can't be read
        fresh = sg.fresh_counts()
        self.assertEqual((fresh["red"]["grubs"], fresh["red"]["towers"]), (3, None))
        # ...and a counter that stops reading goes stale on its own.
        sg.counts_at[("red", "grubs")] = time.time() - 60
        self.assertIsNone(sg.fresh_counts())

    def test_reader_rejects_glitches(self):
        from lolhud.ocr import ScreenGold
        sg = ScreenGold.__new__(ScreenGold)
        sg.values, sg._pending = {"blue": None, "red": None}, {"blue": None, "red": None}
        self.assertTrue(sg._accept("blue", 8000))
        self.assertFalse(sg._accept("blue", 800))     # misread: gold never drops
        self.assertTrue(sg._accept("blue", 8100))
        self.assertFalse(sg._accept("blue", 2500))    # new game? wait for a second read
        self.assertTrue(sg._accept("blue", 2500))


class UpdaterTests(unittest.TestCase):
    def test_version_ordering(self):
        from lolhud.updater import is_newer, parse_version
        self.assertEqual(parse_version("v1.0.12"), (1, 0, 12))
        self.assertTrue(is_newer("v1.0.12", "v1.0.9"))      # numeric, not alphabetical
        self.assertFalse(is_newer("v1.0.9", "v1.0.12"))
        self.assertFalse(is_newer("v1.0.9", "v1.0.9"))
        self.assertTrue(is_newer("v1.0.1", "dev"))          # dev builds are offered releases

    def test_available_needs_the_zip(self):
        from lolhud.updater import Updater
        u = Updater()
        u.current, u.latest = "v1.0.5", "v1.0.6"
        self.assertFalse(u.info()["available"])
        u.asset = {"name": "LeagueHUD-windows.zip", "browser_download_url": "x", "size": 1}
        self.assertTrue(u.info()["available"])


class ProfileTests(unittest.TestCase):
    def test_builtin_scc_profile(self):
        from lolhud import profiles
        listed = {p["id"]: p for p in profiles.listing()}
        self.assertIn("scc-2026", listed)
        self.assertEqual(listed["scc-2026"]["name"], "SCC 2026")
        p = profiles.load("scc-2026")
        fields = {e["field"] for e in p["elements"] if e["type"] == "text"}
        self.assertTrue({"KILLS LEFT", "GOLD RIGHT", "TIMER", "DRAGONS LEFT", "HERALDS RIGHT"} <= fields)
        # Every image the profile uses exists.
        from lolhud.config import web_file
        for e in p["elements"]:
            if "src" in e:
                self.assertIsNotNone(web_file(e["src"].lstrip("/")), e["src"])

    def test_dynamic_and_static_scc(self):
        from lolhud import profiles
        listed = {p["id"]: p for p in profiles.listing()}
        self.assertEqual(listed["scc-2026"]["name"], "SCC 2026")
        self.assertTrue(listed["scc-2026"]["background_video"])
        self.assertEqual(listed["scc-2026-static"]["name"], "SCC 2026 - Static")
        self.assertFalse(listed["scc-2026-static"]["background_video"])
        dynamic = profiles.load("scc-2026")["elements"]
        kinds = [e["type"] for e in dynamic]
        # The video sits above the bar background and below the panels and icons.
        self.assertLess(kinds.index("video"), kinds.index("team-panel"))
        self.assertTrue(any(e.get("hideWithVideo") for e in dynamic))

    def test_byte_ranges(self):
        from lolhud.app import parse_range
        self.assertEqual(parse_range("bytes=0-", 1000), (0, 999))
        self.assertEqual(parse_range("bytes=100-199", 1000), (100, 199))
        self.assertEqual(parse_range("bytes=-50", 1000), (950, 999))
        self.assertIsNone(parse_range("bytes=2000-", 1000))
        self.assertIsNone(parse_range(None, 1000))

    def test_profile_ids_are_safe(self):
        from lolhud import profiles
        self.assertIsNone(profiles.folder("../../etc"))
        self.assertIsNone(profiles.folder("SCC 2026"))

    def test_layer_names(self):
        from lolhud.psd_import import field_for, live_fields, role_for
        fields = live_fields()
        self.assertEqual(field_for("Kills Left", fields), "KILLS LEFT")
        self.assertEqual(field_for("TOWERS RIGHT", fields), "TURRETS RIGHT")
        self.assertEqual(field_for("grubs_left", fields), "VOID GRUB LEFT")
        self.assertIsNone(field_for("Background", fields))
        self.assertEqual(role_for("team logo left"), ("logo", "left", None))
        self.assertEqual(role_for("bo3 2 right WIN"), ("pip", "right", (2, True)))
        self.assertEqual(role_for("TEAM RIGHT"), ("team-panel", "right", None))
        self.assertEqual(role_for("Dragons Icon Left"), ("objective-icon", "left", "dragons"))

    def test_import_scc_psd(self):
        try:
            import psd_tools  # noqa: F401
        except ImportError:
            self.skipTest("psd-tools not installed")
        from lolhud import profiles, psd_import
        with tempfile.TemporaryDirectory() as d:
            old = profiles.PROFILE_DIR
            profiles.PROFILE_DIR = d
            try:
                psd = os.path.join(os.path.dirname(__file__), "..", "design", "lol-hud-SCC27.psd")
                report = psd_import.import_psd(psd, "Test SCC")
                self.assertEqual(report["missing_fields"], [])
                self.assertEqual(report["pips"], {"left": 2, "right": 2})
                p = profiles.load(report["id"])
                kills = next(e for e in p["elements"] if e.get("field") == "KILLS LEFT")
                self.assertEqual((kills["font"], kills["weight"], kills["anchor"]), ("Inter", 700, "middle"))
                self.assertAlmostEqual(kills["size"], 40.0, delta=0.5)
                tri = next(e for e in p["elements"] if e.get("field") == "TRI-CODE LEFT")
                self.assertEqual(tri.get("case"), "upper")
                self.assertTrue(any(e["type"] == "team-panel" for e in p["elements"]))
            finally:
                profiles.PROFILE_DIR = old


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


class ObserverTests(unittest.TestCase):
    def test_gfx_address_forms(self):
        from lolhud.overlay_window import gfx_base_url
        for typed in ("192.168.2.40", "192.168.2.40:8777", "http://192.168.2.40:8777/overlay",
                      " http://192.168.2.40:8777/overlay?timers=0 "):
            self.assertEqual(gfx_base_url(typed), "http://192.168.2.40:8777")
        self.assertEqual(gfx_base_url("gfx-pc:9000"), "http://gfx-pc:9000")
        for bad in ("", "ftp://x", "http://"):
            with self.assertRaises(ValueError):
                gfx_base_url(bad)


if __name__ == "__main__":
    unittest.main()
