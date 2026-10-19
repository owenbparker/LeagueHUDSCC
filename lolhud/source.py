"""Where game data comes from: the real game client, or a simulated game.

Both return the same JSON shape as Riot's Live Client Data API
(GET https://127.0.0.1:2999/liveclientdata/allgamedata), so everything
downstream works the same in demo mode and in a real game.
"""

import json
import random
import ssl
import time
import urllib.error
import urllib.request


class LiveClientSource:
    """Reads Riot's Live Client Data API.

    The API is served by the game client itself while you play or spectate,
    on localhost only, with a self-signed certificate. Certificate checks are
    skipped for that reason, and only for a localhost URL.
    """

    def __init__(self, url):
        self.url = url
        self.last_error = None
        self._ctx = ssl.create_default_context()
        if "127.0.0.1" in url or "localhost" in url:
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE

    def fetch(self):
        try:
            with urllib.request.urlopen(self.url, context=self._ctx, timeout=2) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            self.last_error = None
            return data
        except urllib.error.HTTPError as e:
            # 404 while the game is still loading.
            self.last_error = f"Game client answered {e.code} (game still loading?)"
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            self.last_error = f"No game found at {self.url} ({getattr(e, 'reason', e)})"
        except ValueError as e:
            self.last_error = f"Bad data from game client: {e}"
        return None


class FileSource:
    """Replays a saved allgamedata JSON file (handy for testing graphics)."""

    def __init__(self, path):
        self.path = path
        self.last_error = None

    def fetch(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)


# ---------------------------------------------------------------------------
# Demo mode: a simulated game so the overlay and CSV can be built and tested
# without League running.

_CHAMPS = [
    ("Gnar", "TOP"), ("Vi", "JUNGLE"), ("Ahri", "MIDDLE"), ("Jinx", "BOTTOM"), ("Nautilus", "UTILITY"),
    ("KSante", "TOP"), ("LeeSin", "JUNGLE"), ("Azir", "MIDDLE"), ("Kaisa", "BOTTOM"), ("Rakan", "UTILITY"),
]
_NAMES = ["Atlas", "Wren", "Solace", "Ember", "Quill", "Obstinatus", "Aelune", "Brightvale", "Cinder", "Moth"]
# (itemID, displayName, total cost)
_ITEMS = [
    (3006, "Berserker's Greaves", 1100), (3020, "Sorcerer's Shoes", 1100), (3047, "Plated Steelcaps", 1200),
    (3031, "Infinity Edge", 3450), (3089, "Rabadon's Deathcap", 3600), (3157, "Zhonya's Hourglass", 3250),
    (3071, "Black Cleaver", 3000), (3068, "Sunfire Aegis", 2700), (6672, "Kraken Slayer", 3100),
    (3036, "Lord Dominik's Regards", 3000), (3135, "Void Staff", 3000), (3075, "Thornmail", 2450),
]
_DRAGONS = ["Fire", "Water", "Earth", "Air", "Hextech", "Chemtech"]
# By role: summoner spells (second one), keystone rune.
_ROLE_KIT = {"TOP": ("SummonerTeleport", 8010), "JUNGLE": ("SummonerSmite", 8112), "MIDDLE": ("SummonerTeleport", 8214),
             "BOTTOM": ("SummonerHeal", 8008), "UTILITY": ("SummonerExhaust", 8439)}


class DemoSource:
    """A rough, fast-forwardable simulation of a pro game."""

    def __init__(self, speed=8.0, start_at=60.0, seed=None):
        self.speed = speed
        self.start_at = start_at
        self.rng = random.Random(seed)
        self.last_error = None
        self._wall_start = time.monotonic()
        self._t = 0.0
        self._events = [{"EventID": 0, "EventName": "GameStart", "EventTime": 0.0}]
        self._players = []
        self._gold = []
        for i, (champ, pos) in enumerate(_CHAMPS):
            team = "ORDER" if i < 5 else "CHAOS"
            tag = ("DSG " if team == "ORDER" else "MU ") + _NAMES[i]
            self._players.append({
                "championName": champ, "rawChampionName": f"game_character_displayname_{champ}",
                "isBot": False, "isDead": False, "respawnTimer": 0.0, "level": 1, "position": pos,
                "items": [], "riotId": f"{tag}#DEMO", "riotIdGameName": tag, "summonerName": tag,
                "team": team, "skinID": 0,
                "scores": {"kills": 0, "deaths": 0, "assists": 0, "creepScore": 0, "wardScore": 0.0},
                "summonerSpells": {
                    "summonerSpellOne": {"rawDisplayName": "GeneratedTip_SummonerSpell_SummonerFlash_DisplayName"},
                    "summonerSpellTwo": {"rawDisplayName": f"GeneratedTip_SummonerSpell_{_ROLE_KIT[pos][0]}_DisplayName"}},
                "runes": {"keystone": {"id": _ROLE_KIT[pos][1]}},
            })
            # Everyone starts with a ward trinket (supports swap to a sweeper later).
            self._players[-1]["items"].append({"itemID": 3340, "displayName": "Stealth Ward", "price": 0, "count": 1,
                                               "slot": 6, "canUse": True, "consumable": False})
            self._gold.append(500.0)
        self._turrets = {"ORDER": self._turret_names("T1"), "CHAOS": self._turret_names("T2")}
        self._dragon_alive_at = 300.0
        self._baron_alive_at = 1200.0
        self._grubs_left = 6
        self._next_id = 1
        self.true_gold = {"ORDER": 0, "CHAOS": 0}

    @staticmethod
    def _turret_names(t):
        names = []
        for lane in ("L", "C", "R"):
            for n in ("05", "04", "03"):
                names.append(f"Turret_{t}_{lane}_{n}_A")
        names += [f"Turret_{t}_C_01_A", f"Turret_{t}_C_02_A"]
        return names

    def _event(self, name, **fields):
        self._events.append({"EventID": self._next_id, "EventName": name, "EventTime": round(self._t, 3), **fields})
        self._next_id += 1

    def _team(self, team):
        return [i for i, p in enumerate(self._players) if p["team"] == team]

    def _alive(self, team):
        return [i for i in self._team(team) if not self._players[i]["isDead"]]

    def _give_team(self, team, amount):
        for i in self._team(team):
            self._gold[i] += amount

    def _step(self):
        """Advance the simulation by one second of game time."""
        self._t += 1.0
        t, rng = self._t, self.rng
        for i, p in enumerate(self._players):
            if t > 30:
                self._gold[i] += 2.04
            if p["isDead"]:
                p["respawnTimer"] = max(0.0, p["respawnTimer"] - 1)
                if p["respawnTimer"] == 0:
                    p["isDead"] = False
                continue
            if t > 90 and rng.random() < (0.13 if p["position"] != "UTILITY" else 0.02):
                p["scores"]["creepScore"] += 1
                self._gold[i] += 20
            p["level"] = min(18, 1 + int((t / 60) ** 0.9 * 0.9))
            p["scores"]["wardScore"] += 0.02
            self._buy(i)

        if t > 180 and rng.random() < 0.012:
            killer_team = "ORDER" if rng.random() < 0.55 else "CHAOS"
            victim_team = "CHAOS" if killer_team == "ORDER" else "ORDER"
            killers, victims = self._alive(killer_team), self._alive(victim_team)
            if killers and victims:
                k, v = rng.choice(killers), rng.choice(victims)
                assisters = rng.sample([i for i in killers if i != k], k=min(len(killers) - 1, rng.randint(0, 3)))
                self._players[k]["scores"]["kills"] += 1
                self._gold[k] += 300
                for a in assisters:
                    self._players[a]["scores"]["assists"] += 1
                    self._gold[a] += 150 / max(1, len(assisters))
                vp = self._players[v]
                vp["scores"]["deaths"] += 1
                vp["isDead"] = True
                vp["respawnTimer"] = 6 + t / 60 * 1.2
                self._event("ChampionKill", KillerName=self._players[k]["riotIdGameName"],
                            VictimName=vp["riotIdGameName"],
                            Assisters=[self._players[a]["riotIdGameName"] for a in assisters])

        if t >= self._dragon_alive_at and rng.random() < 0.02:
            team = "ORDER" if rng.random() < 0.5 else "CHAOS"
            killer = self._players[rng.choice(self._team(team))]["riotIdGameName"]
            self._event("DragonKill", DragonType=rng.choice(_DRAGONS), Stolen="False", KillerName=killer)
            self._dragon_alive_at = t + 300

        if 480 <= t < 1200 and self._grubs_left and rng.random() < 0.01:
            team = "ORDER" if rng.random() < 0.5 else "CHAOS"
            for _ in range(min(3, self._grubs_left)):
                killer = self._players[rng.choice(self._team(team))]["riotIdGameName"]
                self._event("HordeKill", KillerName=killer, Stolen="False")
                self._grubs_left -= 1

        if t >= self._baron_alive_at and rng.random() < 0.006:
            team = "ORDER" if rng.random() < 0.5 else "CHAOS"
            killer = self._players[rng.choice(self._team(team))]["riotIdGameName"]
            self._event("BaronKill", KillerName=killer, Stolen="False")
            self._give_team(team, 300)
            self._baron_alive_at = t + 360

        if t > 600 and rng.random() < 0.006:
            team = "ORDER" if rng.random() < 0.55 else "CHAOS"
            enemy = "CHAOS" if team == "ORDER" else "ORDER"
            if self._turrets[enemy]:
                name = self._turrets[enemy].pop(0)
                killer = self._players[rng.choice(self._team(team))]["riotIdGameName"]
                self._event("TurretKilled", TurretKilled=name, KillerName=killer)
                self._give_team(team, 250)

    def _buy(self, i):
        p = self._players[i]
        spent = sum(it["price"] for it in p["items"])
        owned = [it for it in p["items"] if it.get("slot") != 6]
        if len(owned) >= 6:
            return
        item_id, name, cost = self._ITEM_ORDER[i % 4][len(owned) % 6]
        if self._gold[i] - spent >= cost:
            p["items"].append({"itemID": item_id, "displayName": name, "price": cost, "count": 1,
                               "slot": len(owned), "canUse": False, "consumable": False})

    _ITEM_ORDER = [
        [_ITEMS[0], _ITEMS[8], _ITEMS[3], _ITEMS[9], _ITEMS[10], _ITEMS[6]],
        [_ITEMS[1], _ITEMS[4], _ITEMS[5], _ITEMS[10], _ITEMS[6], _ITEMS[11]],
        [_ITEMS[2], _ITEMS[7], _ITEMS[6], _ITEMS[11], _ITEMS[5], _ITEMS[9]],
        [_ITEMS[2], _ITEMS[11], _ITEMS[7], _ITEMS[5], _ITEMS[6], _ITEMS[3]],
    ]

    def fetch(self):
        target = self.start_at + (time.monotonic() - self._wall_start) * self.speed
        while self._t < target:
            self._step()
        self.true_gold = {team: round(sum(self._gold[i] for i in self._team(team))) for team in ("ORDER", "CHAOS")}
        return {
            "activePlayer": {"error": "Spectator mode"},
            "allPlayers": json.loads(json.dumps(self._players)),
            "events": {"Events": list(self._events)},
            "gameData": {"gameMode": "CLASSIC", "gameTime": self._t, "mapName": "Map11",
                         "mapNumber": 11, "mapTerrain": "Default"},
        }
