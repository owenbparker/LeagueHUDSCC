"""Turns raw Live Client data into the numbers the HUD shows."""

import os
import re

from . import gold as goldlib
from .config import WEB_DIR, web_file

SIDES = ("left", "right")
POSITION_ORDER = {"TOP": 0, "JUNGLE": 1, "MIDDLE": 2, "BOTTOM": 3, "UTILITY": 4}
DRAGON_NAMES = {
    "Fire": "Infernal", "Water": "Ocean", "Earth": "Mountain", "Air": "Cloud",
    "Hextech": "Hextech", "Chemtech": "Chemtech", "Elder": "Elder",
}
# Events we know about but don't need. Anything else is listed in the control
# panel so new event names from future patches are easy to spot.
IGNORED_EVENTS = {
    "GameStart", "GameEnd", "MinionsSpawning", "FirstBlood", "FirstBrick", "ChampionKill",
    "Multikill", "Ace", "InhibRespawningSoon", "InhibRespawned",
}


def fmt_clock(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _structure_owner(name):
    """'Turret_T1_L_03_A' / 'Barracks_T2_R1' -> the team that owned it."""
    m = re.search(r"_T(1|2|Order|Chaos)_", name or "", re.IGNORECASE)
    if not m:
        return None
    return "ORDER" if m.group(1).lower() in ("1", "order") else "CHAOS"


def _other(team):
    return "CHAOS" if team == "ORDER" else "ORDER"


class NameIndex:
    """Maps any name a player can appear under in events to their team."""

    def __init__(self, players):
        self.team = {}
        for p in players:
            for key in ("riotIdGameName", "summonerName", "riotId"):
                value = p.get(key)
                if value:
                    self.team[value.lower()] = p.get("team")
                    self.team[value.split("#")[0].lower()] = p.get("team")

    def team_of(self, name):
        if not name:
            return None
        hit = self.team.get(name.lower()) or self.team.get(name.split("#")[0].lower())
        if hit:
            return hit
        # Minions and structures: Minion_T100..., Turret_T200...
        if "T100" in name or "Order" in name:
            return "ORDER"
        if "T200" in name or "Chaos" in name:
            return "CHAOS"
        return None


def _blank_objectives():
    return {"turrets": 0, "inhibs": 0, "grubs": 0, "heralds": 0, "barons": 0, "atakhans": 0,
            "dragons": [], "elders": 0}


def tally_events(events, names):
    obj = {"ORDER": _blank_objectives(), "CHAOS": _blank_objectives()}
    last = {"dragon": None, "baron": None, "elder": None, "herald": None, "grubs": []}
    last_team = {"baron": None, "elder": None}
    unknown = set()

    for ev in events:
        kind = ev.get("EventName", "")
        low = kind.lower()
        t = ev.get("EventTime") or 0.0
        killer_team = names.team_of(ev.get("KillerName"))

        if kind == "TurretKilled":
            owner = _structure_owner(ev.get("TurretKilled"))
            team = _other(owner) if owner else killer_team
            if team:
                obj[team]["turrets"] += 1
        elif kind == "InhibKilled":
            owner = _structure_owner(ev.get("InhibKilled"))
            team = _other(owner) if owner else killer_team
            if team:
                obj[team]["inhibs"] += 1
        elif kind == "DragonKill":
            dtype = ev.get("DragonType", "")
            if dtype == "Elder":
                last["elder"] = t
                last_team["elder"] = killer_team
                if killer_team:
                    obj[killer_team]["elders"] += 1
            elif killer_team:
                obj[killer_team]["dragons"].append(DRAGON_NAMES.get(dtype, dtype))
            last["dragon"] = t
        elif kind == "BaronKill":
            last["baron"] = t
            last_team["baron"] = killer_team
            if killer_team:
                obj[killer_team]["barons"] += 1
        elif kind == "HeraldKill":
            last["herald"] = t
            if killer_team:
                obj[killer_team]["heralds"] += 1
        elif "horde" in low or "grub" in low:
            last["grubs"].append(t)
            if killer_team:
                obj[killer_team]["grubs"] += 1
        elif "atakhan" in low:
            if killer_team:
                obj[killer_team]["atakhans"] += 1
        elif kind not in IGNORED_EVENTS:
            unknown.add(kind)
    return obj, last, last_team, unknown


def _countdown(spawn_at, now):
    if spawn_at is None:
        return ""
    remaining = spawn_at - now
    return "LIVE" if remaining <= 0 else fmt_clock(remaining + 0.999)


def _grubs_spawn(kills, now, timers):
    """When the current/next grub camp spawns, or None once they're gone."""
    per_wave, waves = 3, int(timers["grubs_waves"])
    n = len(kills)
    if n >= per_wave * waves or now >= timers["grubs_despawn"]:
        return None
    wave_start = (n // per_wave) * per_wave
    return timers["grubs_first"] if wave_start == 0 else kills[wave_start - 1] + timers["grubs_respawn"]


def asset_path(rel):
    """Absolute path on disk for vMix; leaves absolute paths and URLs alone."""
    if not rel or rel.startswith(("http://", "https://")) or os.path.isabs(rel):
        return rel or ""
    return web_file(rel) or os.path.normpath(os.path.join(WEB_DIR, rel))


def build_state(raw, cfg, ddragon=None):
    """Return {"fields": flat dict for CSV/vMix, "detail": extra structure}."""
    connected = raw is not None
    raw = raw or {}
    players = raw.get("allPlayers") or []
    events = (raw.get("events") or {}).get("Events") or []
    game_time = float((raw.get("gameData") or {}).get("gameTime") or 0.0)

    side_team = {"left": "ORDER", "right": "CHAOS"}
    if cfg.get("swap_sides"):
        side_team = {"left": "CHAOS", "right": "ORDER"}

    names = NameIndex(players)
    obj, last, last_team, unknown = tally_events(events, names)
    timers = cfg["timers"]

    f = {"STATUS": "LIVE" if connected else "WAITING", "TIMER": fmt_clock(game_time),
         "GAME TIME SECONDS": int(game_time)}

    team_players = {}
    gold_raw = {}
    for side in SIDES:
        team = side_team[side]
        tp = [p for p in players if p.get("team") == team]
        tp.sort(key=lambda p: POSITION_ORDER.get(p.get("position", ""), 9))
        team_players[side] = tp
        o = obj[team]
        if cfg["gold_mode"] == "manual":
            gold_raw[side] = float(cfg["manual_gold"].get(side) or 0)
        else:
            gold_raw[side] = goldlib.team_gold(tp, game_time, cfg["gold_mode"], o["turrets"], o["barons"],
                                               ddragon, cfg.get("gold_estimate_scale", 1.0),
                                               cfg.get("passive_gold_start", goldlib.PASSIVE_START))
        team_cfg = cfg[side]
        S = side.upper()
        f[f"TRI-CODE {S}"] = team_cfg["tricode"]
        f[f"TEAM NAME {S}"] = team_cfg["name"]
        f[f"LOGO {S}"] = asset_path(team_cfg["logo"])
        f[f"COLOR {S}"] = team_cfg["color"]
        f[f"KILLS {S}"] = sum((p.get("scores") or {}).get("kills", 0) for p in tp)
        f[f"TURRETS {S}"] = o["turrets"]
        f[f"GOLD {S}"] = goldlib.format_gold(gold_raw[side], cfg["gold_decimals"], cfg["decimal_separator"])
        f[f"GOLD RAW {S}"] = int(gold_raw[side])
        f[f"VOID GRUB {S}"] = o["grubs"]
        f[f"DRAGONS {S}"] = len(o["dragons"])
        f[f"DRAGON TYPES {S}"] = ",".join(o["dragons"])
        f[f"ELDERS {S}"] = o["elders"]
        f[f"HERALDS {S}"] = o["heralds"]
        f[f"BARONS {S}"] = o["barons"]
        f[f"ATAKHANS {S}"] = o["atakhans"]
        f[f"INHIBS {S}"] = o["inhibs"]
        f[f"SERIES WINS {S}"] = team_cfg["wins"]
        wins_needed = int(cfg["best_of"]) // 2 + 1
        for i in range(1, wins_needed + 1):
            won = team_cfg["wins"] >= i
            f[f"BO{cfg['best_of']} {i} {S} WIN"] = int(won)
            f[f"BO{cfg['best_of']} {i} {S}"] = asset_path(f"assets/pip_{side}{'_win' if won else ''}.png")

    diff = gold_raw["left"] - gold_raw["right"]
    diff_text = "+" + goldlib.format_gold(abs(diff), cfg["gold_decimals"], cfg["decimal_separator"])
    f["GOLD DIFF LEFT"] = diff_text if diff >= 100 else ""
    f["GOLD DIFF RIGHT"] = diff_text if diff <= -100 else ""

    # Objective timers: a countdown, "LIVE", or blank when the objective is
    # gone for the game (or not up yet, for elder) or switched off.
    soul_taken = any(len(obj[t]["dragons"]) >= 4 for t in obj)
    if last["dragon"] is None:
        dragon_spawn = timers["dragon_first"]
    else:
        dragon_spawn = last["dragon"] + (timers["elder_respawn"] if soul_taken else timers["dragon_respawn"])
    herald_gone = last["herald"] is not None or game_time >= timers["herald_despawn"]
    spawns = {
        "grubs": _grubs_spawn(last["grubs"], game_time, timers),
        "dragon": None if soul_taken else dragon_spawn,
        "herald": None if herald_gone else timers["herald_first"],
        "elder": dragon_spawn if soul_taken else None,
        "baron": timers["baron_first"] if last["baron"] is None else last["baron"] + timers["baron_respawn"],
    }
    shown = cfg.get("objective_timers", {})
    for name, spawn_at in spawns.items():
        on = connected and shown.get(name, True)
        f[f"NEXT {name.upper()}"] = _countdown(spawn_at, game_time) if on else ""
    f["NEXT DRAGON IS ELDER"] = int(soul_taken)

    team_side = {v: k for k, v in side_team.items()}
    for buff, length in (("baron", timers["baron_buff"]), ("elder", timers["elder_buff"])):
        active = last[buff] is not None and game_time - last[buff] < length
        holder = team_side.get(last_team[buff]) if active else None
        f[f"{buff.upper()} BUFF SIDE"] = holder.upper() if holder else ""
        f[f"{buff.upper()} BUFF TIME"] = fmt_clock(last[buff] + length - game_time) if holder else ""

    # Players: 1-5 left, 6-10 right.
    n = 0
    for side in SIDES:
        tp = team_players[side] + [{}] * (5 - len(team_players[side]))
        for p in tp[:5]:
            n += 1
            s = p.get("scores") or {}
            f[f"P{n} NAME"] = p.get("riotIdGameName") or p.get("summonerName") or ""
            f[f"P{n} CHAMPION"] = p.get("championName", "")
            f[f"P{n} CHAMPION ICON"] = ddragon.champion_icon(p) if (p and ddragon) else ""
            f[f"P{n} LEVEL"] = p.get("level", "")
            f[f"P{n} KDA"] = f"{s.get('kills', 0)}/{s.get('deaths', 0)}/{s.get('assists', 0)}" if p else ""
            f[f"P{n} CS"] = s.get("creepScore", "") if p else ""
            f[f"P{n} DEAD"] = int(bool(p.get("isDead")))
            f[f"P{n} RESPAWN"] = int(p.get("respawnTimer") or 0) if p.get("isDead") else ""

    detail = {
        "connected": connected,
        "game_time": game_time,
        "side_team": side_team,
        "dragons": {side: obj[side_team[side]]["dragons"] for side in SIDES},
        "unknown_events": sorted(unknown),
    }
    return {"fields": f, "detail": detail}
