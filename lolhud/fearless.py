"""Fearless draft: the champions each team played in the series' earlier
games, which they can't pick again. The SCC 2027 draft shows them by role
from the second game on.

Each finished champ select (all ten picks in, then the lobby closes) is
kept in config "fearless" -> "games", by team (tricode, else name), with the
picks in role order (top, jungle, mid, bot, support: the order of the
lobby's seats). Going back to 0-0 in the series starts a new list.
"""

ROLES = ("top", "jungle", "mid", "bot", "support")

# Shown by the demo draft when nothing's been played yet, so the panel can be
# seen: two made-up earlier games (champion ids).
SAMPLE_GAMES = [
    {"teams": {"_blue": [266, 64, 103, 222, 412], "_red": [122, 254, 7, 51, 89]}},
    {"teams": {"_blue": [24, 121, 61, 202, 117], "_red": [86, 76, 134, 498, 497]}},
]


def team_key(team_cfg, fallback):
    """A team's key in the history: its tricode, else its name, else its side."""
    team_cfg = team_cfg or {}
    return (str(team_cfg.get("tricode") or "").strip().upper()
            or str(team_cfg.get("name") or "").strip().upper() or fallback)


def sides(cfg):
    """(blue team key, red team key) for the teams as set up now."""
    blue, red = ("right", "left") if cfg.get("swap_sides") else ("left", "right")
    keys = team_key(cfg.get(blue), "BLUE"), team_key(cfg.get(red), "RED")
    if keys[0] == keys[1]:   # two teams with the same tricode: told apart by side
        keys = (f"{keys[0]} ({blue.upper()})", f"{keys[1]} ({red.upper()})")
    return keys


def complete(draft):
    """Whether a draft (from draft.convert_session) has all ten champions locked in."""
    if not draft or not draft.get("active"):
        return False
    picks = [p for side in ("blue", "red") for p in (draft.get(side) or {}).get("picks") or []]
    return len(picks) == 10 and all(int(p.get("champion") or 0) and not p.get("active") for p in picks)


def game_of(draft, cfg):
    """The record kept for a finished draft: {"teams": {key: [5 champion ids]}}."""
    blue, red = sides(cfg)
    return {"teams": {blue: [int(p.get("champion") or 0) for p in draft["blue"]["picks"]][:5],
                      red: [int(p.get("champion") or 0) for p in draft["red"]["picks"]][:5]}}


def add(games, game):
    """The list with this game added, unless it's the same as the last one."""
    games = list(games or [])
    if games and sorted(map(tuple, games[-1]["teams"].values())) == sorted(map(tuple, game["teams"].values())):
        return games, False
    return games + [game], True


def view(games, cfg, champion, sample=False):
    """What the draft shows: each side's earlier champions by role (oldest
    first), with images. champion(id) -> {"name", "square", ...}."""
    blue, red = sides(cfg)
    real = list(games or [])
    games = real or ([{"teams": {blue: g["teams"]["_blue"], red: g["teams"]["_red"]}} for g in SAMPLE_GAMES]
                     if sample else [])
    out = {"games": len(games), "sample": bool(games) and not real}
    for side, key in (("blue", blue), ("red", red)):
        roles = [[] for _ in ROLES]
        for g in games:
            picks = g.get("teams", {}).get(key)
            if not picks:
                continue
            for i, cid in enumerate(picks[:5]):
                c = champion(cid) if cid else None
                roles[i].append({"name": c["name"], "square": c["square"]} if c else {"name": "", "square": ""})
        out[side] = roles
    return out
