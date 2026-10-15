"""Bitfocus Companion control (the module is in companion-module/).

    GET  /api/companion   what's on and off, the slider values and live
                          variables (Companion polls this for its feedbacks)
    POST /api/companion   {"action": "toggle" | "on" | "off", "id": "..."}
                          {"action": "set" | "adjust", "id": "bg_speed", "value": 0.5}
                          {"action": "run", "id": "sync_all"}

The same API runs in the GFX Controller and the Observer Client, so a
Stream Deck can drive either (each app only does what its client does: the
observer has "Sync GFX to my time", the GFX PC the HUD's settings).
"""

import time

from .log import log


def _path(cfg, path, default):
    node = cfg
    for key in path[:-1]:
        node = node.get(key) or {}
    value = node.get(path[-1])
    return default if value is None else bool(value)


def _nest(path, value):
    out = value
    for key in reversed(path):
        out = {key: out}
    return out


# Settings that are on or off: id -> (name, config path, default).
TOGGLES = {
    "cams_on": ("Webcams: show on the HUD", ("cams", "enabled"), True),
    "cams_auto": ("Webcams: switch to the players in the action", ("cams", "auto"), True),
    "cams_director": ("Webcams: read this PC's screen", ("cams", "director"), False),
    "first_blood": ("Animation: first blood", ("animations", "first_blood"), True),
    "counter_dragons": ("Claimed objectives: dragons", ("strip_counters", "dragons"), True),
    "counter_heralds": ("Claimed objectives: Rift Heralds", ("strip_counters", "heralds"), True),
    "counter_grubs": ("Claimed objectives: void grubs", ("strip_counters", "grubs"), True),
    "timers_auto": ("Objective timers: auto (only the next few)", ("objective_timers_auto",), False),
    "timer_grubs": ("Objective timer: Void Grubs", ("objective_timers", "grubs"), True),
    "timer_dragon": ("Objective timer: Dragon", ("objective_timers", "dragon"), True),
    "timer_herald": ("Objective timer: Rift Herald", ("objective_timers", "herald"), True),
    "timer_elder": ("Objective timer: Elder Drake", ("objective_timers", "elder"), True),
    "timer_baron": ("Objective timer: Baron", ("objective_timers", "baron"), True),
    "bg_tint": ("Background video: tint with team colours", ("bg_tint",), True),
}
# On/off states that aren't settings.
STATES = {
    "hud_on_game": "HUD on game (the HUD window over League)",
    "time_control": "Time control on (League's Replay API)",
    "connected": "Game connected",
}
# Sliders: id -> (name, config key, default, min, max).
VALUES = {
    "bg_speed": ("Background video: playback speed", "bg_speed", 1.0, 0.1, 3.0),
    "bg_brightness": ("Background video: brightness", "bg_brightness", 1.0, 0.2, 2.5),
    "timers_auto_count": ("Objective timers: how many auto shows (1-3)", "objective_timers_auto_count", 2, 1, 3),
}
COMMANDS = {
    "sync_all": "Match time: sync all clients to 0:00",
    "sync_gfx": "Match time: sync GFX to my time (Observer Client)",
    "enable_time_control": "Match time: turn on time control",
    "preview_fb_left": "Animation: preview first blood, left team",
    "preview_fb_right": "Animation: preview first blood, right team",
}


def _mmss(t):
    t = max(0, int(t))
    return f"{t // 60:02d}:{t % 60:02d}"


def state(app):
    cfg = app.config.get()
    snap = app.snapshot()
    toggles = {k: _path(cfg, p, d) for k, (_, p, d) in TOGGLES.items()}
    ow = app.overlay_window.info() if app.overlay_window else {"open": False}
    toggles["hud_on_game"] = bool(ow.get("open"))
    toggles["time_control"] = app.match_sync.enabled is True
    toggles["connected"] = bool(snap["detail"].get("connected"))
    values = {k: float(cfg.get(key) if cfg.get(key) is not None else d) for k, (_, key, d, _, _) in VALUES.items()}
    f = snap["fields"]
    game_time = float(snap["detail"].get("game_time") or 0)
    if toggles["connected"]:
        game_time += snap.get("game_time_age") or 0
    local = app.match_sync.local or {}
    local_time = ""
    if "time" in local:
        run = 0 if local.get("paused") else (time.time() - local.get("at", time.time())) * local.get("speed", 1)
        local_time = _mmss(local["time"] + run)
    variables = {
        "game_time": _mmss(game_time) if toggles["connected"] else "",
        "this_pc_game_time": local_time,
        "match_time_status": app.match_sync.status or "",
        "role": {"gfx": "GFX", "observer": "Observer"}.get(app.role, ""),
        "bg_speed": f"{values['bg_speed']:.2f}x",
        "bg_brightness": f"{round(values['bg_brightness'] * 100)}%",
        "timers_auto_count": str(int(values["timers_auto_count"])),
    }
    for side in ("left", "right"):
        S = side.upper()
        variables[f"tricode_{side}"] = cfg[side].get("tricode", "")
        for name, field in (("kills", f"KILLS {S}"), ("gold", f"GOLD {S}")):
            variables[f"{name}_{side}"] = str(f.get(field, ""))
    return {"role": app.role, "toggles": toggles, "values": values, "variables": variables}


def act(app, body):
    """Do one Companion action; returns state(app)."""
    action, key = body.get("action"), body.get("id")
    cfg = app.config.get()
    if action in ("toggle", "on", "off"):
        if key == "hud_on_game":
            if not app.overlay_window:
                raise ValueError("The HUD window is only available in the desktop app")
            now = app.overlay_window.window is not None
            want = (not now) if action == "toggle" else action == "on"
            url = cfg.get("observer_url") if app.role == "observer" else None
            app.overlay_window.set(want, None, url)
        elif key in TOGGLES:
            _, path, default = TOGGLES[key]
            now = _path(cfg, path, default)
            want = (not now) if action == "toggle" else action == "on"
            app.config.update(_nest(path, want))
            log(f"Companion: {TOGGLES[key][0]} {'on' if want else 'off'}")
        else:
            raise ValueError(f"Unknown setting: {key}")
    elif action in ("set", "adjust"):
        if key not in VALUES:
            raise ValueError(f"Unknown value: {key}")
        _, ckey, default, lo, hi = VALUES[key]
        value = float(body.get("value") or 0)
        if action == "adjust":
            value += float(cfg.get(ckey) if cfg.get(ckey) is not None else default)
        value = round(max(lo, min(hi, value)), 2)
        app.config.update({ckey: int(round(value)) if key == "timers_auto_count" else value})
    elif action == "run":
        if key == "sync_all":
            app.sync_all()
        elif key == "sync_gfx":
            app.sync_gfx_to_me()
        elif key == "enable_time_control":
            from .matchtime import enable_replay_api
            enable_replay_api()
            app.match_sync.enabled = True
            app.match_sync.status = "Time control on: restart the League client if it was just turned on"
        elif key in ("preview_fb_left", "preview_fb_right"):
            app.preview = {"name": "first_blood", "side": key.rsplit("_", 1)[1], "id": time.time()}
        else:
            raise ValueError(f"Unknown command: {key}")
    else:
        raise ValueError(f"Unknown action: {action}")
    app.refresh(fetch=False)
    return state(app)


def describe():
    """Names of everything, for the module's dropdowns."""
    return {"toggles": {k: v[0] for k, v in TOGGLES.items()}, "states": STATES,
            "values": {k: {"name": v[0], "min": v[3], "max": v[4]} for k, v in VALUES.items()},
            "commands": COMMANDS}
