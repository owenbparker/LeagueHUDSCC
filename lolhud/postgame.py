"""The end-of-game stats: each player's damage to champions and exact gold.

Riot's live game API (what the HUD reads during the game) doesn't report
damage, or any gold but your own. The League client on this PC shows the
end-of-game screen once the game is over, and its own API ("the LCU") has
what that screen shows: GET /lol-end-of-game/v1/eog-stats-block. They're
read here and sent to GFX Engine with the last game's stats, so the post-game
graphics (Team stats, Player stats) have damage and gold.

A PC only has them when its client shows that screen after the game (a
player's client does; a spectating client may not).
"""

import threading
import time

from .draft import LcuClient, find_client
from .log import log

POLL_EVERY = 4


def _name(n):
    return str(n or "").split("#")[0].strip().lower()


def parse(block):
    """The eog-stats-block -> {"game_id", "players": {name: {"dmg", "gold", "kills",
    "deaths", "assists", "cs", "vision"}}}, or None when it has no players."""
    players = {}
    for team in (block or {}).get("teams") or []:
        for p in team.get("players") or []:
            s = p.get("stats") or {}
            entry = {"dmg": int(s.get("TOTAL_DAMAGE_DEALT_TO_CHAMPIONS") or 0), "gold": int(s.get("GOLD_EARNED") or 0),
                     "kills": int(s.get("CHAMPIONS_KILLED") or 0), "deaths": int(s.get("NUM_DEATHS") or 0),
                     "assists": int(s.get("ASSISTS") or 0),
                     "cs": int(s.get("MINIONS_KILLED") or 0) + int(s.get("NEUTRAL_MINIONS_KILLED") or 0),
                     "vision": int(s.get("VISION_SCORE") or 0)}
            for key in ("riotIdGameName", "summonerName", "riotId", "gameName"):
                if p.get(key):
                    players[_name(p[key])] = entry
    if not players:
        return None
    return {"game_id": (block or {}).get("gameId"), "players": players}


def apply(fields, game, gold_text):
    """Puts the end-of-game numbers on the last game's player fields (P1 NAME...)
    where the names match. Returns how many players matched."""
    matched = 0
    for n in range(1, 11):
        p = game["players"].get(_name(fields.get(f"P{n} NAME")))
        if not p:
            continue
        matched += 1
        fields[f"P{n} DMG"] = p["dmg"]
        fields[f"P{n} GOLD RAW"] = p["gold"]
        fields[f"P{n} GOLD"] = gold_text(p["gold"])
        fields[f"P{n} KDA"] = f"{p['kills']}/{p['deaths']}/{p['assists']}"
        fields[f"P{n} CS"] = p["cs"]
        fields[f"P{n} VISION"] = p["vision"]
    return matched


class PostGameReader:
    """Reads the end-of-game stats from this PC's League client, in the background."""

    def __init__(self, extra_paths=lambda: ()):
        self.extra_paths = extra_paths
        self.game = None      # the latest parse(), with "at": when it was read
        self._client = None
        self._seen = None
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _run(self):
        wait = POLL_EVERY
        while not self._stop.wait(wait):
            wait = POLL_EVERY
            try:
                if self._client is None:
                    found = find_client(self.extra_paths())
                    if not found:
                        wait = 30   # (looking for the client starts a process: not every few seconds)
                        continue
                    self._client = LcuClient(*found)
                game = parse(self._client.get("/lol-end-of-game/v1/eog-stats-block"))
                if game and game["game_id"] != self._seen:
                    self._seen = game["game_id"]
                    self.game = dict(game, at=time.time())
                    log(f"End-of-game stats read for {len(game['players'])} players (damage, gold)")
            except Exception:
                self._client = None   # (the client closed or restarted: found again later)
