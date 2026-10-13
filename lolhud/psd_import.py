"""Turns a Photoshop HUD design into a HUD profile.

Layers are matched by name (case doesn't matter), so a designer can build
any look and the HUD keeps working:

  Text layers named after a live field   KILLS LEFT, GOLD RIGHT, TIMER,
                                         TRI-CODE LEFT, TURRETS LEFT, ...
                                         (any field in the control panel's
                                         Live fields list) become live text
                                         in the layer's font, size, colour
                                         and alignment.
  TEAM LEFT / TEAM RIGHT                 filled with the team colour
  TEAM LOGO LEFT / LOGO LEFT             where the team logo goes
  BO3 1 LEFT, BO3 2 LEFT ... (+ WIN)     series pips
  LINES                                  artwork tinted left-to-right in
                                         the team colours
  GOLD DIFF LEFT / RIGHT                 where the gold-lead tab hangs
  OBJECTIVE TIMERS                       top-left corner of the timers
  DRAGONS ICON LEFT, HERALDS ICON LEFT,  objective icons that hide with
  GRUBS ICON LEFT                        their counter

Everything else is artwork, flattened into as few images as possible with
Photoshop's own blending and effects. Fonts aren't stored in a PSD, so
they're looked up in Windows' fonts (and the app's own); any that can't be
found are listed so they can be uploaded.
"""

import glob
import json
import os
import re
import shutil
import tempfile

from . import profiles
from .config import DEFAULTS, WEB_DIR
from .state import build_state

ALIASES = {"TOWERS": "TURRETS", "TOWER": "TURRETS", "GRUBS": "VOID GRUB", "VOID GRUBS": "VOID GRUB",
           "TRICODE": "TRI-CODE", "DRAGON": "DRAGONS", "HERALD": "HERALDS", "BARON": "BARONS",
           "GAME TIME": "TIMER", "CLOCK": "TIMER"}
OBJECTIVE_OF = {"DRAGONS": "dragons", "HERALDS": "heralds", "VOID GRUB": "grubs"}
WEIGHTS = {"thin": 100, "hairline": 100, "extralight": 200, "ultralight": 200, "light": 300,
           "regular": 400, "book": 400, "normal": 400, "roman": 400, "medium": 500, "semibold": 600,
           "demibold": 600, "bold": 700, "extrabold": 800, "ultrabold": 800, "black": 900, "heavy": 900}
IMPORTANT = ["KILLS LEFT", "KILLS RIGHT", "GOLD LEFT", "GOLD RIGHT", "TURRETS LEFT", "TURRETS RIGHT",
             "TIMER", "TRI-CODE LEFT", "TRI-CODE RIGHT"]


def norm(name):
    return re.sub(r"\s+", " ", (name or "").replace("_", " ")).strip().upper()


def live_fields():
    return set(build_state(None, DEFAULTS)["fields"]) | {"TIMER"}


def field_for(name, fields):
    n = norm(name)
    if n in fields:
        return n
    for alias, real in sorted(ALIASES.items(), key=lambda a: -len(a[0])):
        if n == alias or n.startswith(alias + " "):
            candidate = real + n[len(alias):]
            if candidate in fields:
                return candidate
    return None


def role_for(name):
    n = norm(name)
    m = re.fullmatch(r"(?:TEAM )?LOGO (LEFT|RIGHT)", n)
    if m:
        return ("logo", m.group(1).lower(), None)
    m = re.fullmatch(r"TEAM(?: PANEL)? (LEFT|RIGHT)", n)
    if m:
        return ("team-panel", m.group(1).lower(), None)
    m = re.fullmatch(r"(?:BO\s?\d|PIP|SERIES)\s?(\d) (LEFT|RIGHT)( WIN)?", n)
    if m:
        return ("pip", m.group(2).lower(), (int(m.group(1)), bool(m.group(3))))
    if n in ("LINES", "TEAM LINES"):
        return ("lines", None, None)
    m = re.fullmatch(r"GOLD DIFF(?:ERENCE)? (LEFT|RIGHT)", n)
    if m:
        return ("gold-diff", m.group(1).lower(), None)
    if n in ("OBJECTIVE TIMERS", "TIMERS"):
        return ("timers", None, None)
    m = re.fullmatch(r"(DRAGONS?|HERALDS?|(?:VOID )?GRUBS?) ICON (LEFT|RIGHT)", n)
    if m:
        kind = m.group(1)
        key = "dragons" if kind.startswith("DRAGON") else "heralds" if kind.startswith("HERALD") else "grubs"
        return ("objective-icon", m.group(2).lower(), key)
    return None


# --- fonts -------------------------------------------------------------------

def split_ps_name(ps):
    """"Inter-BoldItalic" -> ("Inter", 700, True)."""
    family, _, style = (ps or "").partition("-")
    style_l = style.lower().replace(" ", "")
    italic = "italic" in style_l or "oblique" in style_l
    core = style_l.replace("italic", "").replace("oblique", "")
    weight = WEIGHTS.get(core, 400 if not core else WEIGHTS.get(core, 400))
    # "InterDisplay" style names: put spaces back between words for CSS.
    family = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", family) if " " not in family else family
    return family, weight, italic


def font_dirs():
    dirs = [os.path.join(WEB_DIR, "fonts")]
    if os.name == "nt":
        dirs.append(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
        dirs.append(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"))
    else:
        dirs += ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts")]
    return [d for d in dirs if d and os.path.isdir(d)]


def find_font(ps):
    """Path of a font file for this PostScript name, or None."""
    from PIL import ImageFont
    want = re.sub(r"[^a-z0-9]", "", ps.lower())
    family = re.sub(r"[^a-z0-9]", "", split_ps_name(ps)[0].lower())
    exact, by_family = None, None
    for d in font_dirs():
        for path in glob.glob(os.path.join(d, "**", "*"), recursive=True):
            if not path.lower().endswith((".ttf", ".otf")):
                continue
            stem = re.sub(r"[^a-z0-9]", "", os.path.splitext(os.path.basename(path))[0].lower())
            try:
                fam, style = ImageFont.truetype(path, 12).getname()
            except Exception:
                continue
            f = re.sub(r"[^a-z0-9]", "", (fam or "").lower())
            st = re.sub(r"[^a-z0-9]", "", (style or "").lower())
            if f + st == want or stem == want or (f == family and st in ("", "regular") and want == family):
                return path
            if exact is None and f == family and st and st in want:
                exact = path
            if by_family is None and (f == family or stem.startswith(family)):
                by_family = path  # e.g. a variable font that covers every weight
    return exact or by_family


# --- reading the PSD -----------------------------------------------------------

def _hex(values):
    if not values or len(values) < 4:
        return "#ffffff"
    _, r, g, b = values[:4]
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(v * 255))) for v in (r, g, b))


def _text_element(layer, field):
    ed = layer.engine_dict
    style = ed["StyleRun"]["RunArray"][0]["StyleSheet"]["StyleSheetData"]
    para = ed["ParagraphRun"]["RunArray"][0]["ParagraphSheet"]["Properties"]
    fonts = layer.resource_dict["FontSet"]
    ps = str(fonts[int(style.get("Font", 0))]["Name"]).strip("'\"")
    xx, xy, _, yy, tx, ty = layer.transform
    scale = (xx * xx + xy * xy) ** 0.5 or 1
    size = float(style.get("FontSize", 12)) * scale
    family, weight, italic = split_ps_name(ps)
    if style.get("FauxBold"):
        weight = max(weight, 700)
    anchor = {0: "start", 1: "end", 2: "middle"}.get(int(para.get("Justification", 0)), "start")
    shape = ed["Rendered"]["Shapes"]["Children"][0]["Cookie"]["Photoshop"].get("ShapeType", 0)
    left, top, right, bottom = layer.bbox
    if shape == 0:  # point text: the transform is the anchor on the baseline
        x, y = tx, ty
    else:           # paragraph text: work from the box
        x = {"start": left, "end": right, "middle": (left + right) / 2}[anchor]
        y = top + size * 0.8
    e = {"type": "text", "field": field, "x": round(x, 2), "y": round(y, 2), "font": family,
         "weight": weight, "size": round(size, 2), "anchor": anchor, "color": _hex(style.get("FillColor", {}).get("Values"))}
    if italic:
        e["italic"] = True
    if int(style.get("FontCaps", 0)) == 2:
        e["case"] = "upper"
    tracking = float(style.get("Tracking", 0) or 0)
    if tracking:
        e["letterSpacing"] = round(tracking / 1000 * size, 2)
    width = right - left
    if field.startswith("GOLD ") and not field.startswith("GOLD DIFF"):
        e["maxWidth"] = round(width * 1.15, 1)
    elif field.startswith("TRI-CODE"):
        e["maxWidth"] = round(max(width * 1.6, size * 1.8), 1)
    elif field.startswith("TEAM NAME"):
        e["maxWidth"] = round(width * 2, 1)
    for prefix, key in OBJECTIVE_OF.items():
        if field.startswith(prefix + " "):
            e["objective"] = key
    return e, ps


def import_psd(psd_path, name):
    """Build a profile from a PSD. Returns the import report."""
    from psd_tools import PSDImage

    psd = PSDImage.open(psd_path)
    fields = live_fields()
    pid = profiles.new_id(name)
    out_dir = os.path.join(profiles.PROFILE_DIR, pid)
    os.makedirs(os.path.join(out_dir, "fonts"), exist_ok=True)

    elements, fonts_used, found = [], {}, []
    pips = {"left": {}, "right": {}}
    anchors = {}
    run, images = [], 0
    parents = {}

    def walk(group):
        for layer in group:
            parents[id(layer)] = group
            yield layer
            if layer.is_group():
                yield from walk(layer)

    def ancestors(layer):
        node = parents.get(id(layer))
        while node is not None and node is not psd:
            yield node
            node = parents.get(id(node))

    def save_image(img, stem):
        nonlocal images
        images += 1
        fname = f"{stem}-{images}.png"
        img.save(os.path.join(out_dir, fname), optimize=True)
        return fname

    def flush():
        """Flatten the artwork collected so far into one image."""
        if not run:
            return
        keep = {id(l) for l in run}
        for l in run:
            keep |= {id(a) for a in ancestors(l)}
        img = psd.composite(layer_filter=lambda l: id(l) in keep)
        run.clear()
        if img is None:
            return
        img = img.convert("RGBA")
        box = img.getbbox()
        if box:
            elements.append({"type": "image", "src": save_image(img.crop(box), "art"),
                             "x": box[0], "y": box[1], "w": box[2] - box[0], "h": box[3] - box[1]})

    def layer_image(layer):
        img = layer.composite()
        return img.convert("RGBA") if img is not None else None

    for layer in walk(psd):
        if layer.is_group():
            continue
        role = role_for(layer.name)
        if role and role[0] == "pip":  # pips count even when hidden (WIN variants)
            side, (index, win) = role[1], role[2]
            if not win or index not in pips[side]:
                pips[side][index] = layer.bbox
            continue
        if not layer.is_visible():
            continue
        field = field_for(layer.name, fields) if layer.kind == "type" else None
        if field:
            flush()
            e, ps = _text_element(layer, field)
            elements.append(e)
            fonts_used.setdefault(ps, set()).add(e["font"])
            found.append(field)
            continue
        if not role:
            run.append(layer)
            continue
        flush()
        kind, side, extra = role
        left, top, right, bottom = layer.bbox
        if kind == "logo":
            elements.append({"type": "logo", "side": side, "x": left, "y": top, "w": right - left, "h": bottom - top})
        elif kind in ("team-panel", "lines", "objective-icon"):
            img = layer_image(layer)
            if img is not None:
                e = {"type": "image" if kind == "objective-icon" else kind, "src": save_image(img, kind),
                     "x": left, "y": top, "w": img.width, "h": img.height}
                if side and kind == "team-panel":
                    e["side"] = side
                if kind == "objective-icon":
                    e["objective"] = extra
                elements.append(e)
        elif kind == "gold-diff":
            anchors[("gold-diff", side)] = ((left + right) / 2, top)
        elif kind == "timers":
            anchors["timers"] = (left, top)
        found.append(norm(layer.name))
    flush()

    # Series pips, in order.
    for side, slots in pips.items():
        if slots:
            elements.append({"type": "pips", "side": side, "slots": [
                [b[0], b[1], b[2] - b[0], b[3] - b[1]] for _, b in sorted(slots.items())]})
    # Gold-lead tabs: where the designer put them, else just under the gold.
    for side in ("left", "right"):
        if ("gold-diff", side) in anchors:
            x, y = anchors[("gold-diff", side)]
        else:
            gold = next((e for e in elements if e.get("field") == f"GOLD {side.upper()}"), None)
            if not gold:
                continue
            x, y = gold["x"], gold["y"] + gold["size"] * 0.3
        elements.append({"type": "gold-diff", "side": side, "x": round(x, 1), "y": round(y, 1)})
    x, y = anchors.get("timers", (4, 4))
    elements.append({"type": "timers", "x": x, "y": y})

    # Fonts.
    font_faces, missing = [], []
    for ps, families in fonts_used.items():
        family, weight, italic = split_ps_name(ps)
        path = find_font(ps)
        if not path:
            missing.append(ps)
            continue
        fname = os.path.basename(path)
        shutil.copyfile(path, os.path.join(out_dir, "fonts", fname))
        variable = "variable" in fname.lower() or "[" in fname
        font_faces.append({"family": family, "src": f"fonts/{fname}", "ps": ps,
                           "weight": "100 900" if variable else str(weight),
                           "style": "italic" if italic else "normal"})

    report = {
        "fields": sorted(set(found)),
        "missing_fields": [f for f in IMPORTANT if f not in found],
        "pips": {side: len(slots) for side, slots in pips.items()},
        "logos": [e["side"] for e in elements if e["type"] == "logo"],
        "fonts": sorted(fonts_used),
        "missing_fonts": missing,
        "images": images,
    }
    profiles.save(pid, {"name": name, "canvas": [psd.width, psd.height], "fonts": font_faces,
                        "elements": elements, "missing_fonts": missing, "import_report": report,
                        "source": os.path.basename(psd_path)})
    return {"id": pid, "name": name, **report}


def import_upload(name, filename, data):
    """Import a PSD sent from the control panel (bytes)."""
    if not filename.lower().endswith((".psd", ".psb")):
        raise ValueError("Choose a Photoshop file (.psd)")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, os.path.basename(filename))
        with open(path, "wb") as f:
            f.write(data)
        return import_psd(path, name or os.path.splitext(os.path.basename(filename))[0])


def add_font(pid, filename, data):
    """Add an uploaded font file to an imported profile; clears it from the missing list."""
    from PIL import ImageFont
    profile = profiles.load(pid)
    folder = profiles.folder(pid)
    if not profile or not folder or not folder.startswith(profiles.PROFILE_DIR):
        raise ValueError("Fonts can only be added to imported profiles")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(filename))
    if not name.lower().endswith((".ttf", ".otf", ".woff", ".woff2")):
        raise ValueError("Font must be a .ttf, .otf, .woff or .woff2 file")
    path = os.path.join(folder, "fonts", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    # Which missing font is this? Match by name, else take the first missing one.
    try:
        fam, style = ImageFont.truetype(path, 12).getname()
        key = re.sub(r"[^a-z0-9]", "", (fam + style).lower())
    except Exception:
        key = re.sub(r"[^a-z0-9]", "", os.path.splitext(name)[0].lower())
    missing = profile.get("missing_fonts", [])
    match = next((ps for ps in missing if re.sub(r"[^a-z0-9]", "", ps.lower()) in (key, key.replace("regular", ""))),
                 None) or next((ps for ps in missing if re.sub(r"[^a-z0-9]", "", split_ps_name(ps)[0].lower()) in key),
                               missing[0] if missing else None)
    if match:
        family, weight, italic = split_ps_name(match)
        profile.setdefault("fonts", []).append({"family": family, "src": f"fonts/{name}", "ps": match,
                                                "weight": str(weight), "style": "italic" if italic else "normal"})
        missing.remove(match)
        profile["missing_fonts"] = missing
        if profile.get("import_report"):
            profile["import_report"]["missing_fonts"] = missing
    profiles.save(pid, profile)
    return {"matched": match, "missing_fonts": missing}


if __name__ == "__main__":  # python -m lolhud.psd_import design.psd "Name"
    import sys
    print(json.dumps(import_psd(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "Imported"), indent=2))
