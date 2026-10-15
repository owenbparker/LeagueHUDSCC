"""Match time: keeping every PC's League spectator client at the same moment.

League's Replay API (https://127.0.0.1:2999/replay/playback, on each PC)
reports and sets where a spectator or replay client is in the game. It's
off until game.cfg has EnableReplayApi=1 (see enable_replay_api()).

The GFX PC holds the latest sync command ({"id", "time", "issued", "scope"}).
It applies "all" commands to its own client straight away; observer apps
poll it a few times a second and apply them to theirs, adding on however
long ago the command was given (measured on the GFX PC's clock, which each
observer estimates from its polls), so every client lands on the same moment.
"Sync GFX to my time" is a "gfx" command: only the GFX PC applies it.
"""

import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .log import log

TOO_OLD = 10.0   # seconds: older commands are history, not orders
POLL_EVERY = 0.25


def playback_url(api_url):
    u = urlparse(api_url or "https://127.0.0.1:2999")
    return f"{u.scheme or 'https'}://{u.netloc or '127.0.0.1:2999'}/replay/playback"


class ReplayClient:
    """This PC's League client, through the Replay API."""

    HELP = ("League's Replay API is off or the client isn't spectating. Click \"Turn on time control\", "
            "then restart the League spectator client.")

    def __init__(self, api_url):
        self.url = playback_url(api_url)
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE   # the game's own self-signed certificate, on localhost
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                   urllib.request.HTTPSHandler(context=self._ctx))

    def _call(self, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url, data, {"Content-Type": "application/json"})
        try:
            with self._opener.open(req, timeout=2) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise ValueError(self.HELP if e.code in (403, 404) else f"League answered {e.code}")
        except (urllib.error.URLError, OSError) as e:
            text = str(getattr(e, "reason", e)).lower()
            if "timed out" in text:
                raise ValueError("League didn't answer. Restart the League client after turning on time "
                                 "control, and check it's spectating a game or playing a replay.")
            raise ValueError(self.HELP if "refused" in text or "10061" in text
                             else f"Couldn't reach League's Replay API ({getattr(e, 'reason', e)})")

    def get(self):
        """{"time": seconds, "paused": bool, "speed": 1.0, ...}"""
        return self._call()

    def seek(self, seconds):
        return self._call({"time": max(0.0, float(seconds))})


def game_cfg_candidates():
    paths = []
    for drive in ("C:", "D:", "E:"):
        for base in (r"\Riot Games\League of Legends", r"\Program Files\Riot Games\League of Legends",
                     r"\Program Files (x86)\Riot Games\League of Legends", r"\Games\League of Legends"):
            paths.append(drive + base + r"\Config\game.cfg")
    return paths


def enable_replay_api(path=None):
    """Add EnableReplayApi=1 to League's game.cfg. Returns the file changed
    (None if it was already on). The client must be restarted afterwards."""
    found = [path] if path else [p for p in game_cfg_candidates() if os.path.isfile(p)]
    if not found:
        raise ValueError("Couldn't find League's game.cfg (looked in the usual Riot Games folders). Add "
                         "EnableReplayApi=1 under [General] in League of Legends\\Config\\game.cfg yourself.")
    changed = None
    for cfg in found:
        with open(cfg, encoding="utf-8-sig", errors="replace") as f:
            lines = f.read().splitlines()
        if any(line.strip().lower() == "enablereplayapi=1" for line in lines):
            continue
        lines = [line for line in lines if not line.strip().lower().startswith("enablereplayapi=")]
        at = next((i for i, line in enumerate(lines) if line.strip().lower() == "[general]"), None)
        if at is None:
            lines = ["[General]", "EnableReplayApi=1"] + lines
        else:
            lines.insert(at + 1, "EnableReplayApi=1")
        with open(cfg, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        changed = cfg
    return changed


def replay_api_enabled():
    """Is EnableReplayApi=1 in League's game.cfg? None if it can't be found."""
    found = [p for p in game_cfg_candidates() if os.path.isfile(p)]
    if not found:
        return None
    for cfg in found:
        try:
            with open(cfg, encoding="utf-8-sig", errors="replace") as f:
                if any(line.strip().lower() == "enablereplayapi=1" for line in f):
                    return True
        except OSError:
            pass
    return False


class MatchSync:
    """Sync commands: held here on the GFX PC; followed from it on an observer."""

    def __init__(self, replay):
        self.replay = replay
        self.command = None          # GFX: the latest command
        self._seen = None            # observer: the last command id dealt with
        self._offset = None          # observer: GFX clock minus this PC's clock
        self._best_rtt = None
        self.status = ""
        self.local = {}              # this PC's game: {"time", "paused"} or {"error"}
        self.enabled = None          # EnableReplayApi=1 in game.cfg (None: game.cfg not found)
        self._lock = threading.Lock()

    # --- GFX side ----------------------------------------------------------

    def issue(self, seconds, scope="all"):
        """A new command, applied to this PC's client at once:
        {"ok", "status"}."""
        seconds = max(0.0, float(seconds))
        with self._lock:
            n = (self.command or {}).get("id", 0) + 1
            self.command = {"id": n, "time": seconds, "issued": time.time(), "scope": scope}
        # (Observers follow an "all" command even if this PC's game can't.)
        what = "all clients" if scope == "all" else "the GFX PC to the observer"
        try:
            self.replay.seek(seconds)
            self.status = f"Synced to {fmt(seconds)}"
            log(f"Synced {what}: {fmt(seconds)}")
            return {"ok": True, "status": self.status}
        except ValueError as e:
            self.status = f"This PC's game didn't sync: {e}"
            log(f"Synced {what}: {fmt(seconds)}, but this PC's game didn't: {e}", "warn")
            return {"ok": False, "status": self.status}

    def public(self):
        return {"now": time.time(), "command": self.command}

    # --- observer side -----------------------------------------------------

    def follow_once(self, get_json, base):
        """One poll of the GFX PC; applies a new "all" command."""
        t0 = time.time()
        data = get_json(base + "/api/sync", timeout=2)
        t1 = time.time()
        rtt = t1 - t0
        # Keep the clock offset from the quickest poll (the least uncertain),
        # refreshed now and then.
        offset = float(data.get("now", t1)) - (t0 + t1) / 2
        if self._offset is None or rtt <= (self._best_rtt or 1) * 1.2:
            self._offset, self._best_rtt = offset, rtt
        else:
            self._best_rtt *= 1.01
        cmd = data.get("command")
        if not cmd:
            self._seen = self._seen or 0
            return
        if self._seen is None:      # just connected: earlier commands aren't for us
            self._seen = cmd["id"]
            return
        if cmd["id"] == self._seen:
            return
        self._seen = cmd["id"]
        if cmd.get("scope") != "all":
            return
        elapsed = (time.time() + self._offset) - float(cmd["issued"])
        if elapsed > TOO_OLD:
            return
        try:
            state = self.replay.get()
            if state.get("paused"):
                target = float(cmd["time"])
            else:
                target = float(cmd["time"]) + max(0.0, elapsed) * float(state.get("speed") or 1)
            self.replay.seek(target)
            self.status = f"Synced to {fmt(cmd['time'])} with the GFX PC"
            log(f"Synced to {fmt(cmd['time'])} with the GFX PC")
        except ValueError as e:
            self.status = str(e)
            log(f"Couldn't sync this PC's game: {e}", "warn")

    def mine_plus(self, ahead=0.5):
        """This PC's current time plus `ahead` seconds (for "Sync GFX to my time")."""
        state = self.replay.get()
        return float(state.get("time") or 0) + (0 if state.get("paused") else ahead)


def fmt(seconds):
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


def run_follower(app, stop):
    """Observer apps: keep following the GFX PC's sync commands."""
    from .cams import get_json
    failed, checked = False, 0.0
    while not stop.is_set():
        if time.time() - checked >= 1:   # this PC's own game time, for the panels
            checked = time.time()
            try:
                app.match_sync.enabled = replay_api_enabled()
            except Exception:
                pass
            try:
                st = app.match_sync.replay.get()
                app.match_sync.local = {"time": float(st.get("time") or 0), "paused": bool(st.get("paused"))}
            except Exception as e:
                app.match_sync.local = {"error": str(e)}
        if app.role == "observer" and app.config.get().get("observer_url"):
            try:
                app.match_sync.follow_once(get_json, app._gfx_base())
                failed = False
            except Exception:
                if not failed:
                    app.match_sync._seen = None   # reconnecting: don't replay old commands
                failed = True
        stop.wait(POLL_EVERY)
