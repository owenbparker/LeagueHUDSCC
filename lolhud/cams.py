"""Player webcams on the HUD.

Each team's cam shows one player at a time: whoever the action is on. A
player's cam is either a link (a VDO.Ninja view link, say) shown in the
overlay as-is, or a cam connected straight to the HUD from the player's PC
(the Player Cam client) and sent peer to peer over the local network
(WebRTC), with this app passing the connection messages between them.

Who the action is on comes from, in order:
  1. a cam pinned to a player in the control panel;
  2. the "director": the PC whose game goes to air reads the player name
     tags on its screen and reports the players nearest the middle;
  3. the players in the latest kill.
"""

import difflib
import json
import re
import threading
import time
import unicodedata
import urllib.request
import uuid

from .log import log

SIDES = ("left", "right")
SEATS = [f"{side}-{slot}" for side in SIDES for slot in range(1, 6)]
LONG_POLL = 20          # seconds a poll waits for a message
GONE_AFTER = LONG_POLL + 15
DIRECTOR_FRESH = 6.0    # seconds a director report counts for
FIGHT_FRESH = 12.0      # seconds of game time a kill keeps the cams on its players


def _other(side):
    return "right" if side == "left" else "left"


class CamHub:
    """Passes WebRTC connection messages between player cams (publishers)
    and the overlays showing them (viewers), keeps track of which cam is in
    which seat, and decides which player each team's cam shows."""

    def __init__(self):
        self._cond = threading.Condition()
        self._inbox = {}       # peer id -> [messages]
        self._seen = {}        # peer id -> last poll
        self._seat = {}        # publisher id -> "left-1".."right-5"
        self._label = {}       # publisher id -> camera / PC name
        self.director = {"at": 0.0, "left": None, "right": None, "seen": [], "source": ""}
        self.shown = {"left": None, "right": None}
        self.why = {"left": "", "right": ""}
        self._changed = {"left": 0.0, "right": 0.0}

    # --- connection messages ----------------------------------------------

    def poll(self, peer, seat=None, claim=False, label=None, wait=LONG_POLL):
        """A cam or overlay checking in. Waits up to `wait` seconds for
        messages. A cam passes its seat, and claim=True when its user picks
        one (otherwise the hub's seat stands: "Swap teams" moves cams)."""
        if not peer or len(peer) > 64:
            raise ValueError("Bad peer id")
        deadline = time.time() + max(0.0, min(float(wait), LONG_POLL))
        with self._cond:
            self._touch(peer)
            if seat is not None:
                if seat not in SEATS and seat != "":
                    raise ValueError("Unknown seat")
                if claim or peer not in self._seat:
                    for other, s in list(self._seat.items()):
                        if other != peer and s == seat and seat:
                            self._seat[other] = ""  # one cam per seat: the newest wins
                            self._inbox.setdefault(other, []).append({"from": "hub", "type": "replaced"})
                    if peer not in self._seat or claim:
                        log(f"Player cam {'connected' if peer not in self._seat else 'moved'}: "
                            f"{label or 'camera'} as {seat or 'no seat'}")
                    self._seat[peer] = seat
                if label is not None:
                    self._label[peer] = str(label)[:60]
            while not self._inbox.get(peer) and time.time() < deadline:
                self._cond.wait(deadline - time.time())
                self._touch(peer)
            messages = self._inbox.pop(peer, [])
            return {"seat": self._seat.get(peer), "messages": messages}

    def send(self, to, sender, data):
        if not isinstance(data, dict) or len(json.dumps(data)) > 64 * 1024:
            raise ValueError("Bad message")
        with self._cond:
            if to not in self._seen:
                raise ValueError("That cam or overlay isn't connected")
            box = self._inbox.setdefault(to, [])
            box.append({**data, "from": sender})
            del box[:-50]
            self._cond.notify_all()

    def _touch(self, peer):
        now = time.time()
        self._seen[peer] = now
        for p, t in list(self._seen.items()):
            if now - t > GONE_AFTER:
                for d in (self._seen, self._inbox, self._label):
                    d.pop(p, None)
                if self._seat.pop(p, None):
                    log("Player cam disconnected")

    def swap_sides(self):
        """Teams swapped left/right: their cams go with them."""
        with self._cond:
            for p, seat in self._seat.items():
                if seat:
                    side, slot = seat.split("-")
                    self._seat[p] = f"{_other(side)}-{slot}"
            self.shown = {"left": self.shown["right"], "right": self.shown["left"]}

    def publishers(self):
        """{"left-1": {"id": ..., "label": ...}} for the cams connected now."""
        with self._cond:
            now = time.time()
            return {seat: {"id": p, "label": self._label.get(p, "")} for p, seat in self._seat.items()
                    if seat and now - self._seen.get(p, 0) < GONE_AFTER}

    # --- who's on screen ----------------------------------------------------

    def report(self, focus, seen=None, source="screen"):
        """The director's latest read: {"left": slot or None, "right": ...}."""
        self.director = {"at": time.time(), "left": focus.get("left"), "right": focus.get("right"),
                         "seen": seen or [], "source": source}

    def resolve(self, cfg, detail):
        """Which player (1-5) each team's cam shows now, with the reason."""
        cams = cfg.get("cams", {})
        now = time.time()
        fresh = now - self.director["at"] < DIRECTOR_FRESH
        fight = detail.get("fight")
        fight_on = bool(fight) and 0 <= detail.get("game_time", 0) - fight["time"] < FIGHT_FRESH
        for side in SIDES:
            pin = int((cams.get("pin") or {}).get(side) or 0)
            want, why = None, ""
            if 1 <= pin <= 5:
                want, why = pin, "pinned"
            elif cams.get("auto", True):
                if fresh and self.director.get(side):
                    want, why = self.director[side], "on screen"
                elif fight_on and fight.get(side):
                    want, why = fight[side], "latest kill"
            if want is None or want == self.shown[side]:
                if want is not None:
                    self.why[side] = why
                continue
            if why == "pinned" or self.shown[side] is None or now - self._changed[side] >= float(cams.get("hold", 3)):
                self.shown[side], self.why[side], self._changed[side] = want, why, now
        return {"left": self.shown["left"], "right": self.shown["right"], "why": dict(self.why)}

    def info(self, cfg, detail):
        return {"focus": self.resolve(cfg, detail), "publishers": self.publishers(),
                "director": {k: self.director[k] for k in ("left", "right", "seen", "source")}
                | {"age": round(time.time() - self.director["at"], 1) if self.director["at"] else None}}


# --- reading name tags ------------------------------------------------------

_CONFUSABLE = str.maketrans({"0": "o", "1": "l", "|": "l", "!": "l", "5": "s", "$": "s", "8": "b"})


def normalise(text):
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.lower()).translate(_CONFUSABLE)


def _names_match(name, line):
    """Is the (normalised) name in the (normalised) OCR line? Allows a
    misread letter in longer names."""
    if len(name) < 3 or not line:
        return False
    if name in line:
        return True
    if len(name) < 5 or len(line) < len(name) - 1:
        return False
    for size in (len(name) - 1, len(name), len(name) + 1):
        for i in range(0, max(1, len(line) - size + 1)):
            if difflib.SequenceMatcher(None, name, line[i:i + size]).ratio() >= 0.83:
                return True
    return False


def find_players(lines, roster):
    """Players whose name tag is in the OCR lines.

    lines: [(text, (x, y, w, h)), ...]; roster: {"left": [{"slot", "names", ...}], ...}.
    Returns [{"side", "slot", "x", "y"}] with the centre of each tag found.
    """
    found = {}
    norm_lines = [(normalise(t), box) for t, box in lines]
    for side in SIDES:
        for p in roster.get(side) or []:
            if p.get("dead"):
                continue
            names = {normalise(n) for n in p.get("names") or [] if n}
            if p.get("champion_name"):
                names.add(normalise(p["champion_name"]))
            for text, (x, y, w, h) in norm_lines:
                if any(_names_match(n, text) for n in names):
                    key = (side, p["slot"])
                    found.setdefault(key, []).append((x + w / 2, y + h / 2))
    return [{"side": s, "slot": slot, "x": round(x), "y": round(y)}
            for (s, slot), spots in found.items() for x, y in spots]


def pick_focus(seen, centre):
    """Each team's player nearest the middle of the screen, or None."""
    cx, cy = centre
    best = {}
    for p in seen:
        # The tag sits above the champion; aim at the champion.
        d = (p["x"] - cx) ** 2 + (p["y"] + 60 - cy) ** 2
        if p["side"] not in best or d < best[p["side"]][0]:
            best[p["side"]] = (d, p["slot"])
    return {side: best[side][1] if side in best else None for side in SIDES}


class Director:
    """Reads the player name tags off this PC's League screen and reports
    which player of each team is nearest the middle (Windows only)."""

    INTERVAL = 0.6

    def __init__(self, config, get_roster, send):
        self.config = config
        self.get_roster = get_roster   # -> {"left": [...], "right": [...]} or None
        self.send = send               # (focus, seen) -> None
        self.status = "off"
        self.last = None
        self._pending = {}
        self._steady = {"left": None, "right": None}
        self._stop = threading.Event()
        self._ocr = None
        self.enabled = lambda: False   # set by the app

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _engine(self):
        if self._ocr is None:
            import sys
            if sys.platform != "win32":
                raise RuntimeError("Reading the screen only works on Windows")
            from .ocr import WindowsOcr, make_dpi_aware
            make_dpi_aware()
            self._ocr = WindowsOcr()
        return self._ocr

    def read_once(self):
        """One read: (focus, seen, image, lines) or raises with the reason."""
        from .ocr import capture_league
        roster = self.get_roster()
        if not roster or not any(roster.get(s) for s in SIDES):
            raise RuntimeError("No game: waiting for the players' names")
        ocr = self._engine()
        captured, reason = capture_league(full=True)
        if not captured:
            raise RuntimeError(reason)
        image, height = captured
        k = image.height / 1080
        ax, ay, aw, ah = self.config.get()["cams"].get("director_area") or [120, 110, 1680, 690]
        # The area is for a 16:9 screen, centred like the game's HUD.
        left = (image.width - 1920 * k) / 2
        box = (int(left + ax * k), int(ay * k), int(left + (ax + aw) * k), int((ay + ah) * k))
        crop = image.crop(box)
        # Small text reads better bigger, but Windows' text recognition takes
        # at most 2600 pixels a side.
        scale = min(2.0, 2590 / max(crop.size))
        if abs(scale - 1) > 0.01:
            crop = crop.resize((round(crop.width * scale), round(crop.height * scale)))
        lines = [(t, (box[0] + x / scale, box[1] + y / scale, w / scale, h / scale))
                 for t, (x, y, w, h) in ocr.read_lines(crop)]
        seen = find_players(lines, roster)
        focus = pick_focus(seen, (image.width / 2, image.height / 2))
        return focus, seen, image, lines

    def _run(self):
        while not self._stop.is_set():
            if not self.enabled():
                self.status = "off"
                self._stop.wait(1)
                continue
            try:
                focus, seen, _, _ = self.read_once()
                # A new player must be seen twice running, so one misread
                # doesn't switch the cam.
                for side in SIDES:
                    if focus[side] is None or focus[side] == self._pending.get(side):
                        self._steady[side] = focus[side]
                self._pending = focus
                self.send(dict(self._steady), seen)
                names = ", ".join(f"{p['side']} {p['slot']}" for p in seen) or "no name tags"
                self.status = f"Reading the screen: {names}"
            except Exception as e:
                self.status = str(e) or repr(e)
            self._stop.wait(self.INTERVAL)


def post_json(url, body, timeout=3):
    """POST to another PC on the network (never through a web proxy)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def get_json(url, timeout=3):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as r:
        return json.loads(r.read())


def new_id():
    return uuid.uuid4().hex[:16]
