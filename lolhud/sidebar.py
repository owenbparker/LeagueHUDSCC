"""Reads each player's health and mana off the spectator side bars.

Riot's live game data has no health or mana, but the spectator screen shows
both teams down the sides (blue on the left, red on the right): each
player's portrait with a green health bar and a mana bar (blue, or yellow
energy, red fury...) under it. This measures how full each bar is from the
same capture of the League window as the top bar's gold (ocr.py), so it
needs no text recognition: a filled part is coloured, an empty part dark.

    python -m lolhud.sidebar screenshot.png    print the bars of a saved screenshot
"""

import sys

# Where the bars are on a 1920x1080 spectator screen. x is the distance from
# that side's edge to the start and end of the bar (both sides fill from left
# to right); y is the middle row of the first player's bars, and each next
# player is STEP rows lower. They scale with the window's height.
REF_HEIGHT = 1080
BARS = {"x": [31, 73], "hp_y": 209, "mana_y": 218, "step": 102.75}
ROWS = 650          # rows of the screen the bars need (1080p)
PLAYERS = 5


def _lit(p):
    """A filled part of a bar: a strong colour, or near white (a shield)."""
    hi, lo = max(p), min(p)
    return (hi > 85 and hi - lo > 45) or lo > 150


def _dark(p):
    return max(p) < 70


def fill(image, x0, x1, y, s):
    """How full the bar between columns x0..x1 on row y is (0-1), or None
    when this doesn't look like a bar (the side bars hidden, something over
    them)."""
    px = image.load()
    rows = [r for r in (y - round(s), y, y + round(s)) if 0 <= r < image.height]
    lit = dark = 0
    marks = []
    for x in range(x0 + 1, x1 - 1):   # (the ends blend into the frame)
        votes = [_lit(px[x, r]) for r in rows]
        if sum(votes) * 2 > len(votes):
            lit += 1
            marks.append(1)
        elif all(_dark(px[x, r]) for r in rows):
            dark += 1
            marks.append(0)
        else:
            marks.append(None)
    n = len(marks)
    if not n or (lit + dark) < n * 0.8:
        return None
    # A bar fills from the left: lit columns after the first dark run are noise.
    stray = sum(1 for i, m in enumerate(marks) if m == 1 and 0 in marks[:i])
    if stray > max(2, n * 0.1):
        return None
    return round(lit / n, 3)


def read_bars(image, height=None, bars=None):
    """{"blue": [{"hp": 0.92, "mana": 0.4}, ...5], "red": [...]} from a
    capture of the League window (at least its top ROWS rows at 1080p).
    A bar that can't be read is None."""
    from PIL import Image  # noqa: F401  (image is a PIL image)
    height = height or image.height
    b = bars or BARS
    s = height / REF_HEIGHT
    rgb = image.convert("RGB")
    out = {}
    for team in ("blue", "red"):
        a, z = round(b["x"][0] * s), round(b["x"][1] * s)
        x0, x1 = (a, z) if team == "blue" else (rgb.width - z, rgb.width - a)
        players = []
        for i in range(PLAYERS):
            row = {}
            for kind in ("hp", "mana"):
                y = round((b[kind + "_y"] + i * b["step"]) * s)
                row[kind] = fill(rgb, x0, x1, y, s) if y < rgb.height else None
            players.append(row)
        out[team] = players
    return out


def plausible(bars):
    """True when most health bars read: the side bars are on screen."""
    hp = [p["hp"] for t in bars.values() for p in t]
    return sum(v is not None for v in hp) >= 6


def main(argv):
    from PIL import Image
    image = Image.open(argv[0])
    for team, players in read_bars(image).items():
        print(team, " ".join(f"{p['hp']}/{p['mana']}" for p in players))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
