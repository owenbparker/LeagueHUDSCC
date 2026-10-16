"""Champion select (the draft), for the /draft overlay.

Read from the League client's own API (the "LCU") on a PC that is in the
lobby, a player's or a spectator's: GET /lol-champ-select/v1/session. That
PC is this one (the GFX PC), or an Observer Client that sends what it reads
here (POST /api/draft/ingest).

Every change is kept with the moment it arrived, so the overlay can show the
draft as it was a set time ago (the spectator delay): bans and picks then
come up one by one at the same pace as they did in the client, and the phase
timer runs as it did.

The look is the "Europe" layout of RCVolus' lol-pick-ban-ui (MIT licence,
see web/draft/LICENSE-lol-pick-ban-ui.txt).
"""

import base64
import copy
import json
import math
import os
import re
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from .log import log

POLL_EVERY = 0.5
KEEP_FOR = 15 * 60   # seconds of history kept (more than any delay)


# --- the League client ------------------------------------------------------

def lockfile_candidates():
    paths = []
    for drive in ("C:", "D:", "E:"):
        for base in (r"\Riot Games\League of Legends", r"\Program Files\Riot Games\League of Legends",
                     r"\Program Files (x86)\Riot Games\League of Legends", r"\Games\League of Legends"):
            paths.append(drive + base + r"\lockfile")
    return paths


def _from_process():
    """(port, password) from the running client's command line (Windows)."""
    if sys.platform != "win32":
        return None
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"name='LeagueClientUx.exe'\").CommandLine"],
            capture_output=True, text=True, timeout=8, creationflags=0x08000000).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    port = re.search(r"--app-port=(\d+)", out or "")
    token = re.search(r"--remoting-auth-token=([\w-]+)", out or "")
    return (int(port.group(1)), token.group(1)) if port and token else None


def find_client(extra_paths=()):
    """(port, password) of the League client running on this PC, or None."""
    for path in list(extra_paths) + lockfile_candidates():
        try:
            with open(path, encoding="utf-8") as f:
                parts = f.read().strip().split(":")
            # name:pid:port:password:protocol
            if len(parts) >= 5:
                return int(parts[2]), parts[3]
        except (OSError, ValueError):
            continue
    return _from_process()


class LcuClient:
    def __init__(self, port, password):
        self.base = f"https://127.0.0.1:{port}"
        auth = base64.b64encode(f"riot:{password}".encode()).decode()
        self.headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE   # the client's own self-signed certificate, on this PC
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                  urllib.request.HTTPSHandler(context=ctx))

    def get(self, path):
        """The JSON at path, or None when there's nothing (404: not in champ select)."""
        req = urllib.request.Request(self.base + path, headers=self.headers)
        try:
            with self.opener.open(req, timeout=3) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise


# --- what the overlay draws -------------------------------------------------

def phase_name(actions):
    """BAN PHASE 1 / PICK PHASE 1 / BAN PHASE 2 / PICK PHASE 2 (as RCVolus)."""
    for i, a in enumerate(actions):
        if not a.get("completed"):
            if a.get("type") == "ban":
                return "BAN PHASE 1" if i <= 6 else "BAN PHASE 2"
            return "PICK PHASE 1" if i <= 12 else "PICK PHASE 2"
    return ""


def convert_session(session, names=None, now=None):
    """The LCU session -> {"phase", "timer_left", "blue": {...}, "red": {...}}.

    A team: {"picks": [{"champion", "active", "spell1", "spell2", "name"}],
    "bans": [{"champion", "active"}], "active"}, champions as numeric ids
    (0: none yet)."""
    names = names or {}
    actions = [a for group in session.get("actions") or [] for a in group]
    current = next((a for a in actions if not a.get("completed")), None)
    cells = list(session.get("myTeam") or []) + list(session.get("theirTeam") or [])
    # Sides: a cell's "team" (1 blue, 2 red); without it, myTeam is blue.
    blue_ids, red_ids = set(), set()
    for i, cell in enumerate(cells):
        team = cell.get("team")
        mine = i < len(session.get("myTeam") or [])
        (blue_ids if team == 1 or (team not in (1, 2) and mine) else red_ids).add(cell.get("cellId"))
    out = {"phase": phase_name(actions), "active": True}
    for side, ids in (("blue", blue_ids), ("red", red_ids)):
        team = {"picks": [], "bans": [], "active": False}
        for cell in sorted((c for c in cells if c.get("cellId") in ids), key=lambda c: c.get("cellId", 0)):
            picking = bool(current and current.get("type") == "pick" and current.get("actorCellId") == cell.get("cellId"))
            champ = cell.get("championId") or (current.get("championId") if picking else 0) or 0
            team["picks"].append({"champion": int(champ or 0), "active": picking,
                                  "spell1": int(cell.get("spell1Id") or 0), "spell2": int(cell.get("spell2Id") or 0),
                                  "name": names.get(cell.get("summonerId")) or cell.get("gameName") or ""})
            team["active"] = team["active"] or picking
        for a in actions:
            if a.get("type") != "ban" or a.get("actorCellId") not in ids:
                continue
            banning = a is current
            team["bans"].append({"champion": int(a.get("championId") or 0) if a.get("completed") else 0,
                                 "active": banning})
            team["active"] = team["active"] or banning
        out[side] = team
    timer = session.get("timer") or {}
    left = float(timer.get("adjustedTimeLeftInPhase") or 0) / 1000
    if now is not None and timer.get("internalNowInEpochMs"):
        # A recording: count from when the session was taken.
        left -= max(0.0, now - float(timer["internalNowInEpochMs"]) / 1000)
    out["timer_left"] = max(0.0, left)
    out["timer_infinite"] = bool(timer.get("isInfinite"))
    return out


class DraftFeed:
    """The draft as it arrives, kept with the time it arrived, so it can be
    shown as it was `delay` seconds ago."""

    def __init__(self):
        self.timeline = []   # [(arrived at, draft or None (champ select over))]
        self.source = ""     # "this PC" / "observer" / "demo"
        self.received_at = 0.0
        self._lock = threading.Lock()

    def push(self, draft, source="", at=None):
        """A new reading: a draft from convert_session(), or None when champ
        select isn't on."""
        at = at or time.time()
        with self._lock:
            self.received_at, self.source = at, source or self.source
            if draft is not None:
                draft = dict(draft, timer_end=at + float(draft.get("timer_left") or 0))
            last = self.timeline[-1][1] if self.timeline else None
            if _same(last, draft):
                return
            self.timeline.append((at, draft))
            while len(self.timeline) > 2 and self.timeline[1][0] < at - KEEP_FOR:
                self.timeline.pop(0)

    def clear(self):
        with self._lock:
            self.timeline = []

    def view(self, delay=0.0):
        """What the overlay shows now: the draft as it was `delay` seconds ago,
        with "timer" (seconds left then), or {"active": False}."""
        now = time.time()
        shown_at = now - max(0.0, float(delay or 0))
        with self._lock:
            draft = None
            for at, d in self.timeline:
                if at > shown_at:
                    break
                draft = d
        if not draft:
            return {"active": False}
        out = copy.deepcopy(draft)   # the caller fills in names and images
        # (Rounded up, as League counts down: 30 when a phase starts.)
        out["timer"] = 0 if out.get("timer_infinite") else max(0, math.ceil(out["timer_end"] - shown_at - 0.05))
        out.pop("timer_end", None)
        out.pop("timer_left", None)
        return out

    def info(self, delay=0.0):
        live = self.timeline[-1][1] if self.timeline else None
        return {"source": self.source, "age": round(time.time() - self.received_at, 1) if self.received_at else None,
                "live_active": bool(live), "live_phase": (live or {}).get("phase", ""),
                "shown_active": bool(self.view(delay).get("active")), "delay": delay}


def _same(a, b):
    """Equal apart from the timer's exact end (it shifts a little each read)."""
    if (a is None) != (b is None):
        return False
    if a is None:
        return True
    strip = lambda d: {k: v for k, v in d.items() if k not in ("timer_end", "timer_left")}
    return strip(a) == strip(b) and abs(a.get("timer_end", 0) - b.get("timer_end", 0)) < 1.5


class DraftReader:
    """Reads champ select on this PC's League client, twice a second, and
    hands each reading to send(draft or None)."""

    def __init__(self, send, extra_paths=lambda: ()):
        self.send = send
        self.extra_paths = extra_paths
        self.enabled = False
        self.status = "off"
        self._client = None
        self._names = {}
        self._stop = threading.Event()
        self._thread = None

    def set_enabled(self, on):
        self.enabled = bool(on)
        if self.enabled and not (self._thread and self._thread.is_alive()):
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        elif not self.enabled:
            self._stop.set()
            self.status = "off"

    def _names_for(self, session):
        for cell in (session.get("myTeam") or []) + (session.get("theirTeam") or []):
            sid = cell.get("summonerId")
            if sid and sid not in self._names:
                try:
                    s = self._client.get(f"/lol-summoner/v1/summoners/{sid}") or {}
                    self._names[sid] = s.get("gameName") or s.get("displayName") or ""
                except Exception:
                    self._names[sid] = ""
        return self._names

    def _run(self):
        last_status = None
        while not self._stop.is_set():
            try:
                if self._client is None:
                    found = find_client(self.extra_paths())
                    if not found:
                        raise ConnectionError("League client not found on this PC")
                    self._client = LcuClient(*found)
                session = self._client.get("/lol-champ-select/v1/session")
                if session:
                    draft = convert_session(session, self._names_for(session))
                    self.status = f"Champ select: {draft['phase'] or 'finishing'}"
                else:
                    draft = None
                    self._names = {}
                    self.status = "Connected to the League client (not in champ select)"
                self.send(draft)
            except ConnectionError as e:
                self._client = None
                self.status = str(e)
            except Exception as e:
                self._client = None   # the client restarted: find it again
                self.status = f"Couldn't read champ select: {e}"
            if self.status != last_status:
                log(f"Draft: {self.status}", "warn" if "Couldn't" in self.status or "not found" in self.status else "info")
                last_status = self.status
            self._stop.wait(POLL_EVERY if self._client else 3)


def play_recording(path, send, stop, speed=1.0):
    """Replay a recorded champ select (lol-pick-ban-ui's format: dataPoints
    of {time, isChampSelectActive, session}) at its own pace."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    names = {s.get("summonerId"): s.get("displayName", "") for s in data.get("summoners", [])}
    points = data.get("dataPoints") or []
    from datetime import datetime

    def t(p):
        return datetime.fromisoformat(p["time"].replace("Z", "+00:00")).timestamp()

    if not points:
        return
    start, began = t(points[0]), time.time()
    for p in points:
        wait = (t(p) - start) / speed - (time.time() - began)
        if wait > 0 and stop.wait(wait):
            return
        if p.get("isChampSelectActive") and p.get("session"):
            send(convert_session(p["session"], names, now=t(p)))
        else:
            send(None)
    stop.wait(8)
    if not stop.is_set():
        send(None)
