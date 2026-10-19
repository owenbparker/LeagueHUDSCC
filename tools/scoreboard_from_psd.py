"""Makes the scoreboard and bottom third (web/assets/scoreboard/) from the
designer's HUD PSD (design/lol-hud-SCC27.psd):

    frame.png        the bottom third's artwork (y 788 down), empty slots and all
    loop_left.png    the lined frames round the champion info (left) and the
    loop_right.png   minimap (right): the background loop plays in their shape,
                     and they stand in for it when there's no loop
    sponsor.png      the sponsor logo in the design, shown when no sponsors are set
    bar_hp.png ...   the resource bars, filled (the overlay crops them)
    layout.json      where every player's champion, items, spells, numbers... go

    python tools/scoreboard_from_psd.py design/lol-hud-SCC27.psd

Slots are found by their groups and sorted by position (top to bottom; items
from the champion outwards), so the layer names inside don't matter.
"""

import json
import os
import re
import sys

from psd_tools import PSDImage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "web", "assets", "scoreboard")
TOP = 788          # the bottom third starts here
CENTER = 960

# Groups of slots, one per player (left team, right team).
SLOT_GROUPS = {
    "champ": ("champions left", "champ icons right"),
    "ult": ("ults left", "ults right"),
    "ward": ("wards left", "wards riight"),
    "role": ("role-quest-left", "role-quest-right"),
    "keystone": ("rune-keystone-left", "rune-keystone-right"),
}
TEXT_GROUPS = {
    "name": ("player-names-left", "player-names-right"),
    "cs": ("cs-score-left", "cs-score-right"),
    "kda": ("kda-left", "kda-right"),
    "vision": ("vision-score-left", "vision-score-right"),
}
# Lined frames that the background loop plays in, like the HUD bar's lines.
LOOP_AREAS = {"left": "champion-info-holder", "right": "minimap-cover"}
# Shown live, so left out of the frame.
DYNAMIC = {*LOOP_AREAS.values(), "HUD BAR", "TEAM LEFT", "TEAM RIGHT", "team logo left", "team logo right", "sponsor-logo", "objs", "objs-alive"}


def walk(layer):
    for child in layer:
        yield child
        if child.is_group():
            yield from walk(child)


def rect(layer):
    l, t, r, b = layer.bbox
    return [l, t - TOP, r - l, b - t]


def text_info(layer):
    run = layer.engine_dict["StyleRun"]["RunArray"][0]["StyleSheet"]["StyleSheetData"]
    para = layer.engine_dict["ParagraphRun"]["RunArray"][0]["ParagraphSheet"]["Properties"]
    xx, _, _, _, tx, ty = layer.transform
    size = round(float(run.get("FontSize", 16)) * xx, 1)
    align = {0: "start", 1: "end", 2: "middle"}.get(int(para.get("Justification", 0)), "start")
    l, t, r, b = layer.bbox
    x = {"start": l, "end": r, "middle": (l + r) / 2}[align]
    return {"x": round(x, 1), "y": round(ty - TOP, 1), "size": size, "anchor": align}


def main(psd_path):
    psd = PSDImage.open(psd_path)
    board = psd[0] if psd[0].kind == "artboard" else psd
    groups = {l.name: l for l in walk(board) if l.is_group()}
    layout = {"top": TOP, "players": [{} for _ in range(10)]}

    def per_player(names, fn):
        for side, gname in enumerate(names):
            items = [l for l in groups[gname] if not l.is_group()]
            items.sort(key=lambda l: l.bbox[1])
            for i, layer in enumerate(items[:5]):
                fn(layer, side * 5 + i)

    for key, names in SLOT_GROUPS.items():
        per_player(names, lambda layer, n, key=key: layout["players"][n].__setitem__(key, rect(layer)))
    for key, names in TEXT_GROUPS.items():
        per_player(names, lambda layer, n, key=key: layout["players"][n].__setitem__(key, text_info(layer)))
    # Items: a group per player, item 1 next to the champion.
    for side, gname in enumerate(("items slots left", "item slots right")):
        rows = sorted((g for g in groups[gname] if g.is_group()), key=lambda g: g.bbox[1])
        for i, row in enumerate(rows[:5]):
            slots = sorted(row, key=lambda l: abs((l.bbox[0] + l.bbox[2]) / 2 - CENTER))
            layout["players"][side * 5 + i]["items"] = [rect(s) for s in slots]
    for side, gname in enumerate(("summoner spells left", "summoners spells right")):
        rows = sorted((g for g in groups[gname] if g.is_group()), key=lambda g: g.bbox[1])
        for i, row in enumerate(rows[:5]):
            layout["players"][side * 5 + i]["spells"] = [rect(s) for s in sorted(row, key=lambda l: l.bbox[1])]
    for side, gname in enumerate(("resource-bars-left", "resource-bar-right")):
        rows = sorted((g for g in groups[gname] if g.is_group()), key=lambda g: g.bbox[1])
        for i, row in enumerate(rows[:5]):
            bars = {}
            for b in row:
                kind = re.match(r"(hp|mana|xp)", b.name.lower())
                if kind:
                    bars[kind.group(1)] = rect(b)
            layout["players"][side * 5 + i]["bars"] = bars
    diffs = sorted((l for l in groups["gold-diff-roles"] if l.kind == "type"), key=lambda l: l.bbox[1])
    layout["gold_diff"] = [dict(text_info(l), anchor="middle", x=round((l.bbox[0] + l.bbox[2]) / 2, 1)) for l in diffs]

    # Fonts and colours of the numbers, from the first of each.
    os.makedirs(OUT, exist_ok=True)
    # The resource bars, filled, from the first player's.
    first = sorted((g for g in groups["resource-bars-left"] if g.is_group()), key=lambda g: g.bbox[1])[0]
    for b in first:
        kind = re.match(r"(hp|mana|xp)", b.name.lower())
        if kind:
            b.composite().save(os.path.join(OUT, f"bar_{kind.group(1)}.png"))
    sponsor = next((l for l in walk(board) if l.name == "sponsor-logo"), None)
    if sponsor is not None:
        sponsor.composite().save(os.path.join(OUT, "sponsor.png"))
        layout["sponsor_logo"] = rect(sponsor)
    holder = next((l for l in walk(board) if l.name == "sponsor-holder"), None)
    if holder is not None:
        layout["sponsor_holder"] = rect(holder)
    layout["loop_areas"] = []
    for side, name in LOOP_AREAS.items():
        layer = next((l for l in walk(board) if l.name == name), None)
        if layer is not None:
            layer.composite().save(os.path.join(OUT, f"loop_{side}.png"), optimize=True)
            layout["loop_areas"].append({"side": side, "rect": rect(layer), "src": f"loop_{side}.png"})
    # The frame: everything in the bottom third that isn't filled in live.
    hide = set()
    for layer in walk(board):
        if layer.name in DYNAMIC or layer.kind == "type" or layer.bbox[3] <= TOP:
            hide.add(id(layer))
        if layer.is_group() and layer.name.startswith("resource-bar"):
            hide.add(id(layer))
    for layer in walk(board):
        if id(layer) in hide:
            layer.visible = False
    frame = board.composite(force=True)
    w, h = frame.size
    frame.crop((0, TOP, w, h)).save(os.path.join(OUT, "frame.png"), optimize=True)
    with open(os.path.join(OUT, "layout.json"), "w", encoding="utf-8") as f:
        json.dump(layout, f, indent=1)
    print("Wrote", OUT)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "design", "lol-hud-SCC27.psd"))
