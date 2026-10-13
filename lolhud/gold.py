"""Team gold.

Riot's Live Client Data API does not report other players' gold, so the
HUD works it out one of three ways (set "gold_mode" in the control panel):

- "estimate": models gold earned from game time, CS, kills, assists and
  objectives, never going below the value of the items a player holds.
  Usually lands within about 10% of the real figure.
- "items": the value of the items each player is holding (gold spent).
  Always lower than the real figure, but never wrong in the other direction.
- "manual": numbers typed into the control panel.
"""

import json
import os
import threading
import urllib.request

DDRAGON = "https://ddragon.leagueoflegends.com"

# Rough per-unit gold values used by the estimate.
START_GOLD = 500
PASSIVE_PER_SEC = 2.04
PASSIVE_START = 65
GOLD_PER_CS = 21
GOLD_PER_KILL = 300
GOLD_PER_ASSIST = 75
TEAM_GOLD_PER_TURRET = 400
TEAM_GOLD_PER_BARON = 1500


class DataDragon:
    """Item prices and the patch version for champion icons.

    Downloaded in the background once at start-up and cached on disk, so the
    HUD keeps working offline after the first run.
    """

    def __init__(self, cache_dir):
        self.version = "15.20.1"
        self.item_totals = {}
        self.cache_path = os.path.join(cache_dir, "ddragon_cache.json")
        self._load_cache()
        threading.Thread(target=self._refresh, daemon=True).start()

    def _load_cache(self):
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                cached = json.load(f)
            self.version = cached["version"]
            self.item_totals = {int(k): v for k, v in cached["items"].items()}
        except (OSError, ValueError, KeyError):
            pass

    def _get(self, url):
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _refresh(self):
        try:
            version = self._get(f"{DDRAGON}/api/versions.json")[0]
            items = self._get(f"{DDRAGON}/cdn/{version}/data/en_US/item.json")["data"]
            totals = {int(k): v["gold"]["total"] for k, v in items.items() if k.isdigit()}
        except Exception:
            return
        self.version, self.item_totals = version, totals
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump({"version": version, "items": totals}, f)
        except OSError:
            pass

    def champion_icon(self, player):
        # rawChampionName ends in the internal key (e.g. MonkeyKing for Wukong),
        # which is what Data Dragon names its images after.
        key = (player.get("rawChampionName") or "").rsplit("_", 1)[-1] or player.get("championName", "")
        return f"{DDRAGON}/cdn/{self.version}/img/champion/{key}.png"


def item_value(player, ddragon=None):
    total = 0
    for item in player.get("items") or []:
        price = (ddragon.item_totals.get(item.get("itemID")) if ddragon else None)
        if price is None:
            price = item.get("price") or 0
        total += price * max(1, item.get("count") or 1)
    return total


def estimate_player(player, game_time):
    s = player.get("scores") or {}
    return (START_GOLD
            + PASSIVE_PER_SEC * max(0.0, game_time - PASSIVE_START)
            + GOLD_PER_CS * (s.get("creepScore") or 0)
            + GOLD_PER_KILL * (s.get("kills") or 0)
            + GOLD_PER_ASSIST * (s.get("assists") or 0))


def team_gold(players, game_time, mode, turrets=0, barons=0, ddragon=None, scale=1.0):
    held = sum(item_value(p, ddragon) for p in players)
    if mode == "items":
        return held
    per_player = sum(max(estimate_player(p, game_time), item_value(p, ddragon)) for p in players)
    team_bonus = TEAM_GOLD_PER_TURRET * turrets + TEAM_GOLD_PER_BARON * barons
    return max(held, (per_player + team_bonus) * scale)


def format_gold(amount, decimals=1, separator="."):
    text = f"{amount / 1000:.{decimals}f}"
    return text.replace(".", separator) + "K"
