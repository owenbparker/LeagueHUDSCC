"""Settings for the HUD, saved to config.json next to the project.

Everything here can be changed from the control panel while the app runs.
"""

import copy
import json
import os
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT, "web")
CONFIG_PATH = os.path.join(ROOT, "config.json")

DEFAULTS = {
    # Where the game data comes from. Riot's Live Client Data API runs on the
    # machine that is playing or spectating the game.
    "api_url": "https://127.0.0.1:2999/liveclientdata/allgamedata",
    "poll_interval": 1.0,
    "http_host": "127.0.0.1",
    "http_port": 8777,
    "output_dir": "output",

    "left": {"name": "Team Left", "tricode": "SCC", "logo": "assets/logo_left.png", "wins": 0},
    "right": {"name": "Team Right", "tricode": "SCC", "logo": "assets/logo_right.png", "wins": 0},
    "best_of": 3,
    # By default the left of the HUD is blue side (ORDER). Turn this on when
    # the left-hand team is playing red side.
    "swap_sides": False,

    # "estimate" (model of earned gold), "items" (value of items held) or
    # "manual" (typed into the control panel).
    "gold_mode": "estimate",
    "manual_gold": {"left": 0, "right": 0},
    # Multiplies the estimate if you find it runs consistently high or low.
    "gold_estimate_scale": 1.0,
    # 31.0K style. Set decimal_separator to "," for 31,0K.
    "gold_decimals": 1,
    "decimal_separator": ".",

    # Objective spawn timers in seconds of game time. Riot moves these between
    # seasons, so check them against the current patch.
    "timers": {
        "dragon_first": 300,
        "dragon_respawn": 300,
        "elder_respawn": 360,
        "baron_first": 1200,
        "baron_respawn": 360,
        "baron_buff": 180,
        "elder_buff": 150,
    },
}

_lock = threading.Lock()


def _merge(base, extra):
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self.data = copy.deepcopy(DEFAULTS)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.data = _merge(DEFAULTS, json.load(f))

    def get(self):
        with _lock:
            return copy.deepcopy(self.data)

    def update(self, changes):
        with _lock:
            self.data = _merge(self.data, changes)
            snapshot = copy.deepcopy(self.data)
        self.save(snapshot)
        return snapshot

    def save(self, snapshot=None):
        snapshot = snapshot if snapshot is not None else self.get()
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)
        os.replace(tmp, self.path)

    def output_dir(self):
        out = self.get()["output_dir"]
        return out if os.path.isabs(out) else os.path.join(ROOT, out)
