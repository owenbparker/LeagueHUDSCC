"""Settings for the HUD, saved to config.json next to the project (or next
to LeagueHUD.exe in the desktop build).

Everything here can be changed from the control panel while the app runs.
"""

import copy
import json
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FROZEN = getattr(sys, "frozen", False)
# Read-only files shipped with the app (overlay, fonts, images). In the .exe
# build these are unpacked by PyInstaller.
RESOURCE_DIR = getattr(sys, "_MEIPASS", ROOT) if FROZEN else ROOT
# Things the app writes: settings, uploaded logos, output files.
DATA_DIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else ROOT
WEB_DIR = os.path.join(RESOURCE_DIR, "web")
LOGO_DIR = os.path.join(DATA_DIR, "logos")
PROFILE_DIR = os.path.join(DATA_DIR, "profiles")  # imported HUD profiles
MEDIA_DIR = os.path.join(DATA_DIR, "media")        # background videos
SPONSOR_DIR = os.path.join(DATA_DIR, "sponsors")   # uploaded sponsor logos (served as sponsors/...)
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

DEFAULTS = {
    # Where the game data comes from. Riot's Live Client Data API runs on the
    # machine that is playing or spectating the game.
    "api_url": "https://127.0.0.1:2999/liveclientdata/allgamedata",
    "poll_interval": 1.0,
    "http_host": "127.0.0.1",
    "http_port": 8777,
    # The same over https, for players' browsers to send their cams from
    # (0 = http_port + 2, so 8779).
    "https_port": 0,
    # Let other PCs on your network read the overlay and data (changes can
    # still only be made on this PC).
    "allow_network": False,
    # Let Bitfocus Companion on another PC control this app (see lolhud/companion.py).
    "companion_lan": False,
    "output_dir": "output",

    "left": {"name": "Team Left", "tricode": "SCC", "logo": "assets/logo_left.png", "wins": 0, "color": "#0a6d4c",
             # Webcam links (e.g. VDO.Ninja view links) for players 1-5: top, jungle, mid, bot, support.
             "cam_links": ["", "", "", "", ""]},
    "right": {"name": "Team Right", "tricode": "SCC", "logo": "assets/logo_right.png", "wins": 0, "color": "#bd1313",
              "cam_links": ["", "", "", "", ""]},
    # Size and position tweaks per logo file: {"assets/logos/x.png": {"scale": 1.2, "x": 0, "y": -2}}.
    # Stored per logo, so a team's logo keeps its tweak whichever side it is on.
    "logo_adjust": {},
    "best_of": 3,
    # Which HUD design the overlay draws (see the HUD profile section).
    "profile": "scc-2026",
    # Background loop for profiles that have one (e.g. "SCC 2026"): a video
    # chosen in the control panel (stored in media/), and its playback speed.
    # "" shows the static lines instead.
    "bg_video": "profiles/scc-2026/bg_loop.mp4",
    "bg_speed": 1.0,
    # Tint the video with the team colours, blending left to right like the
    # static lines (off: the video's own colours).
    "bg_tint": True,
    # Brightness of the video lines (1.0 = as filmed / tinted).
    "bg_brightness": 1.0,
    # The sponsor box: covers the spectator's observer controls (camera mode
    # and fog of war, bottom left) with the background loop, tinted in both
    # teams' colours, and sponsor logos in turn. rect is x, y, w, h on the
    # 1920x1080 overlay. bg_video: "" the default loop, "hud" the HUD's video,
    # "image" a still picture (bg_image), "colours" just the team colours.
    "sponsor_box": {"enabled": True, "rect": [0, 964, 304, 116], "sponsors": [], "seconds": 5,
                    "bg_video": "", "bg_image": "", "bg_speed": 1.0, "bg_tint": True, "bg_brightness": 1.0,
                    "logo_size": 0.75, "outline": False},
    # Which screen the "HUD on the game" window covers (0 = main screen).
    "overlay_screen": 0,
    # Observer client: the GFX PC whose HUD to show, e.g. http://192.168.2.40:8777/overlay
    "observer_url": "",
    # Player webcams along the bottom of the HUD: one per team, showing the
    # player the action is on. Each player's cam is a link (VDO.Ninja etc.,
    # in the team's cam_links) or a cam connected straight to the HUD from
    # the player's PC (Player Cam client).
    "cams": {
        "enabled": True,
        # Switch each team's cam to their player closest to the action.
        "auto": True,
        # Shortest time a cam stays on one player before switching (seconds).
        "hold": 3.0,
        # Keep a team's cam on one player (1-5), or 0 to switch automatically.
        "pin": {"left": 0, "right": 0},
        # Read this PC's League screen (the player name tags) to see who's on
        # camera. Run it on the PC whose game goes to air (GFX or observer).
        "director": False,
        # Where to look for name tags on a 1920x1080 screen: x, y, w, h (keeps
        # clear of the HUD at the top and the scoreboard at the bottom).
        "director_area": [120, 110, 1680, 690],
        # Where the cams sit on the 1920x1080 overlay: x, y, w, h.
        "slots": {"left": [288, 821, 174, 259], "right": [1458, 821, 174, 259]},
    },
    # Overlay animations that can be switched off in the control panel.
    "animations": {"first_blood": True},
    # Claimed-objective counters in the HUD (dragons, heralds, grubs).
    "strip_counters": {"dragons": True, "heralds": True, "grubs": True},
    # By default the left of the HUD is blue side (ORDER). Turn this on when
    # the left-hand team is playing red side.
    "swap_sides": False,

    # "screen" (read off the spectator top bar; Windows, falls back to the
    # estimate when it can't be read), "estimate" (model of earned gold),
    # "items" (value of items held) or "manual" (typed into the control panel).
    "gold_mode": "screen",
    # Where the gold numbers are on a 1920x1080 spectator screen, as x, y, w, h.
    # Scaled for other resolutions. Only change if the Test read misses them.
    "gold_ocr_boxes": {"blue": [804, 16, 54, 24], "red": [1106, 16, 54, 24]},
    # Same, for the dragon / herald / grub / tower counters on the top bar.
    "counter_ocr_boxes": {
        "blue": {"dragons": [517, 16, 26, 24], "heralds": [592, 16, 26, 24], "grubs": [662, 16, 26, 24],
                 "towers": [742, 16, 26, 24]},
        "red": {"towers": [1185, 16, 26, 24], "grubs": [1250, 16, 26, 24], "heralds": [1328, 16, 26, 24],
                "dragons": [1407, 16, 26, 24]},
    },
    "manual_gold": {"left": 0, "right": 0},
    # Multiplies the estimate if you find it runs consistently high or low.
    "gold_estimate_scale": 1.0,
    # When passive gold starts (seconds). Earlier since the 2026 season (was
    # 1:05); 0:35 matches the spectator top bar at 1:46 in a real game.
    "passive_gold_start": 35,
    # 31.0K style. Set decimal_separator to "," for 31,0K.
    "gold_decimals": 1,
    "decimal_separator": ".",

    # Objective spawn timers in seconds of game time. Riot moves these between
    # seasons, so check them against the current patch.
    # Defaults follow the 2026 season (patch 26.1).
    "timers": {
        "dragon_first": 300,
        "dragon_respawn": 300,
        "elder_respawn": 360,      # after the 4th dragon (soul), and between elders
        "grubs_first": 480,
        "grubs_waves": 1,          # camps of 3 per game
        "grubs_respawn": 240,      # only used if grubs_waves > 1
        "grubs_despawn": 885,
        "herald_first": 900,
        "herald_despawn": 1185,
        "baron_first": 1200,
        "baron_respawn": 360,
        "baron_buff": 180,
        "elder_buff": 150,
    },
    # The next dragon's element for its timer icon: "" works it out (known
    # once the rift has formed, after the third dragon), or Infernal, Ocean,
    # Mountain, Cloud, Hextech or Chemtech to set it.
    "next_dragon": "",
    # Which objective timers to show, in the order they appear on the overlay.
    "objective_timers": {"grubs": True, "dragon": True, "herald": True, "elder": True, "baron": True},
    # Of grubs, herald and baron, only show the next few still to come (1-3).
    "objective_timers_auto": False,
    "objective_timers_auto_count": 2,
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


# The background loop that ships with the app.
DEFAULT_BG_VIDEO = DEFAULTS["bg_video"]


class Config:
    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self.data = copy.deepcopy(DEFAULTS)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            # Settings saved before the app shipped a loop have no video:
            # give them the default once (removing it afterwards sticks).
            if not saved.get("bg_default_given") and not saved.get("bg_video"):
                saved["bg_video"] = DEFAULT_BG_VIDEO
            self.data = _merge(DEFAULTS, saved)
        self.data["bg_default_given"] = True

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
        return out if os.path.isabs(out) else os.path.join(DATA_DIR, out)


def web_file(rel):
    """Path on disk for a URL path like "assets/logos/x.png", or None.

    Uploaded logos live in LOGO_DIR (served as assets/logos/...); everything
    else comes from WEB_DIR. Paths that try to climb out are refused.
    """
    rel = rel.replace("\\", "/").lstrip("/")
    candidates = []
    if rel.startswith("assets/logos/"):
        candidates.append((LOGO_DIR, rel[len("assets/logos/"):]))
    if rel.startswith("profiles/"):
        candidates.append((PROFILE_DIR, rel[len("profiles/"):]))
    if rel.startswith("media/"):
        candidates.append((MEDIA_DIR, rel[len("media/"):]))
    if rel.startswith("sponsors/"):
        candidates.append((SPONSOR_DIR, rel[len("sponsors/"):]))
    candidates.append((WEB_DIR, rel))
    for base, sub in candidates:
        path = os.path.normpath(os.path.join(base, sub))
        if path.startswith(os.path.normpath(base) + os.sep) and os.path.isfile(path):
            return path
    return None
