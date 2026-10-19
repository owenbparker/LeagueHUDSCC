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
import re
import threading
import urllib.request

DDRAGON = "https://ddragon.leagueoflegends.com"

# Rough per-unit gold values used by the estimate.
START_GOLD = 500
PASSIVE_PER_SEC = 2.04
PASSIVE_START = 35  # 2026 season (was 1:05); see passive_gold_start in config
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
        self.champions = {}   # numeric key -> {"id": "MonkeyKing", "name": "Wukong"}
        self.spells = {}      # numeric key -> image file name (SummonerFlash.png)
        self.runes = {}       # rune id -> icon path (perk-images/...)
        self.ults = {}        # champion id (MonkeyKing) -> its R's image file name
        self._ult_fetching = set()
        self.cache_path = os.path.join(cache_dir, "ddragon_cache.json")
        self._load_cache()
        threading.Thread(target=self._refresh, daemon=True).start()

    def _load_cache(self):
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                cached = json.load(f)
            self.version = cached["version"]
            self.item_totals = {int(k): v for k, v in cached["items"].items()}
            self.champions = {int(k): v for k, v in cached.get("champions", {}).items()}
            self.spells = {int(k): v for k, v in cached.get("spells", {}).items()}
            self.runes = {int(k): v for k, v in cached.get("runes", {}).items()}
            self.ults = dict(cached.get("ults", {}))
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
            champs = self._get(f"{DDRAGON}/cdn/{version}/data/en_US/champion.json")["data"]
            champions = {int(c["key"]): {"id": c["id"], "name": c["name"]} for c in champs.values()}
            spells = self._get(f"{DDRAGON}/cdn/{version}/data/en_US/summoner.json")["data"]
            spells = {int(sp["key"]): sp["image"]["full"] for sp in spells.values() if str(sp.get("key", "")).isdigit()}
            runes = {}
            for tree in self._get(f"{DDRAGON}/cdn/{version}/data/en_US/runesReforged.json"):
                runes[int(tree["id"])] = tree["icon"]
                for slot in tree.get("slots", []):
                    for r in slot.get("runes", []):
                        runes[int(r["id"])] = r["icon"]
        except Exception:
            return
        if version != self.version:
            self.ults = {}   # (a new patch can change an ult's picture)
        self.version, self.item_totals, self.champions, self.spells, self.runes = version, totals, champions, spells, runes
        self._save()

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump({"version": self.version, "items": self.item_totals, "champions": self.champions,
                           "spells": self.spells, "runes": self.runes, "ults": self.ults}, f)
        except OSError:
            pass

    def champion(self, key):
        """Name and images of a champion by its numeric id (as champion select
        reports it), or None."""
        c = self.champions.get(int(key or 0))
        if not c:
            return None
        cdn = f"{DDRAGON}/cdn"
        return {"key": int(key), "name": c["name"], "id": c["id"],
                "square": f"{cdn}/{self.version}/img/champion/{c['id']}.png",
                "loading": f"{cdn}/img/champion/loading/{c['id']}_0.jpg",
                "splash": f"{cdn}/img/champion/splash/{c['id']}_0.jpg"}

    def spell_icon(self, key):
        name = self.spells.get(int(key or 0))
        return f"{DDRAGON}/cdn/{self.version}/img/spell/{name}" if name else ""

    def item_icon(self, item_id):
        return f"{DDRAGON}/cdn/{self.version}/img/item/{int(item_id)}.png" if item_id else ""

    def spell_icon_by_name(self, raw):
        """A summoner spell's icon from the live game's name for it
        ("GeneratedTip_SummonerSpell_SummonerFlash_DisplayName")."""
        m = re.search(r"_(Summoner[A-Za-z0-9]+)_(?:DisplayName|Description)", raw or "")
        return f"{DDRAGON}/cdn/{self.version}/img/spell/{m.group(1)}.png" if m else ""

    def rune_icon(self, rune_id):
        icon = self.runes.get(int(rune_id or 0))
        return f"{DDRAGON}/cdn/img/{icon}" if icon else ""

    def ult_icon(self, champ_key):
        """The champion's ultimate's icon; looked up in the background the
        first time ("" until then)."""
        if not champ_key:
            return ""
        name = self.ults.get(champ_key)
        if name:
            return f"{DDRAGON}/cdn/{self.version}/img/spell/{name}"
        if champ_key not in self._ult_fetching:
            self._ult_fetching.add(champ_key)
            threading.Thread(target=self._fetch_ult, args=(champ_key,), daemon=True).start()
        return ""

    def _fetch_ult(self, champ_key):
        try:
            data = self._get(f"{DDRAGON}/cdn/{self.version}/data/en_US/champion/{champ_key}.json")["data"]
            self.ults[champ_key] = next(iter(data.values()))["spells"][3]["image"]["full"]
            self._save()
        except Exception:
            pass
        finally:
            self._ult_fetching.discard(champ_key)

    @staticmethod
    def champion_key(player):
        """Data Dragon's id for a live player's champion (MonkeyKing for Wukong)."""
        return (player.get("rawChampionName") or "").rsplit("_", 1)[-1] or player.get("championName", "")

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


def estimate_player(player, game_time, passive_start=PASSIVE_START):
    s = player.get("scores") or {}
    return (START_GOLD
            + PASSIVE_PER_SEC * max(0.0, game_time - passive_start)
            + GOLD_PER_CS * (s.get("creepScore") or 0)
            + GOLD_PER_KILL * (s.get("kills") or 0)
            + GOLD_PER_ASSIST * (s.get("assists") or 0))


def team_gold(players, game_time, mode, turrets=0, barons=0, ddragon=None, scale=1.0,
              passive_start=PASSIVE_START):
    held = sum(item_value(p, ddragon) for p in players)
    if mode == "items":
        return held
    per_player = sum(max(estimate_player(p, game_time, passive_start), item_value(p, ddragon)) for p in players)
    team_bonus = TEAM_GOLD_PER_TURRET * turrets + TEAM_GOLD_PER_BARON * barons
    return max(held, (per_player + team_bonus) * scale)


def format_gold(amount, decimals=1, separator="."):
    text = f"{amount / 1000:.{decimals}f}"
    return text.replace(".", separator) + "K"
