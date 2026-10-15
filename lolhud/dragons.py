"""Which dragon has spawned, from the "Dragon has spawned!" banner.

League's game data only names a dragon once it's killed, so until the rift
forms (the third dragon) the HUD can't know the next one's element. The
game does show it, though: the banner in the middle of the screen has the
dragon's portrait. This tells the element from the portrait's colours:

    Infernal  fiery orange and red, strongly coloured
    Mountain  brown stone (warm but dull)
    Ocean     blue-green          Hextech  blue and violet
    Chemtech  toxic green         Cloud    pale, nearly white

and says nothing when the colours don't clearly point one way. Each banner
it reads is saved in the app's dragon-banners folder, for checking.
"""

import colorsys
import os
import re
import time

from .log import log

# On a 1920x1080 screen (centred, scaled by height like the top bar).
BANNER_TEXT = [800, 222, 320, 38]    # "Dragon has spawned!"
PORTRAIT = (958, 167, 34)            # centre x, y and radius of the round portrait
TOP_ROWS = 270                       # rows of the screen the banner needs


def _box(box, width, height):
    from .ocr import scaled_box
    return scaled_box(box, width, height)


def _rgb(img):
    """[(r, g, b), ...] for an RGB image."""
    data = img.tobytes()
    return list(zip(data[0::3], data[1::3], data[2::3]))


def banner_showing(image, height):
    """Cheap check: is there a line of the banner's beige text?"""
    crop = image.crop(_box(BANNER_TEXT, image.width, height)).convert("RGB")
    crop = crop.resize((max(1, crop.width // 2), max(1, crop.height // 2)))
    pixels = _rgb(crop)
    beige = sum(1 for r, g, b in pixels if r > 190 and g > 165 and b < 190 and r - b > 30)
    return beige / max(1, len(pixels)) > 0.06


def portrait_colours(image, height):
    """Shares of the portrait's colours: {"warm", "green", "teal", "blue",
    "whitish", "saturation"}."""
    s = height / 1080
    cx, cy, r = PORTRAIT
    x = image.width / 2 + (cx - 960) * s
    y, r = cy * s, r * s
    crop = image.crop((round(x - r), round(y - r), round(x + r), round(y + r))).convert("RGB")
    size = crop.width
    counts = {"warm": 0, "green": 0, "teal": 0, "blue": 0, "whitish": 0}
    coloured = total = 0
    sat = 0.0
    for i, (R, G, B) in enumerate(_rgb(crop)):
        px, py = i % size - size / 2, i // size - size / 2
        if px * px + py * py > (size / 2) ** 2:
            continue
        h, sv, v = colorsys.rgb_to_hsv(R / 255, G / 255, B / 255)
        h *= 360
        total += 1
        sat += sv
        if sv < 0.2 and v > 0.7:
            counts["whitish"] += 1
        if sv <= 0.35 or v <= 0.25:
            continue
        coloured += 1
        if h < 45 or h >= 320:
            counts["warm"] += 1
        elif 60 <= h < 150:
            counts["green"] += 1
        elif 150 <= h < 200:
            counts["teal"] += 1
        elif 200 <= h < 320:
            counts["blue"] += 1
    out = {k: (v / coloured if coloured else 0.0) for k, v in counts.items() if k != "whitish"}
    out["whitish"] = counts["whitish"] / max(1, total)
    out["saturation"] = sat / max(1, total)
    return out


def classify(c):
    """The dragon's element from portrait_colours(), or None if unsure."""
    if c["whitish"] > 0.3:
        return "Cloud"
    if c["warm"] > 0.6:
        return "Infernal" if c["saturation"] >= 0.5 else "Mountain"
    for key, name in (("green", "Chemtech"), ("teal", "Ocean"), ("blue", "Hextech")):
        if c[key] > 0.5:
            return name
    return None


def read_banner(image, ocr, height, folder=None):
    """(element or None, banner text) if a dragon's spawn banner is up, else None."""
    if not banner_showing(image, height):
        return None
    from .ocr import prepare
    crop = image.crop(_box(BANNER_TEXT, image.width, height))
    text = ocr.read(prepare(crop, 2))
    low = text.lower()
    if not re.search(r"drag|drake", low) or "spawn" not in low:
        return None
    if "elder" in low:
        return "Elder", text
    element = classify(portrait_colours(image, height))
    if folder:
        try:
            os.makedirs(folder, exist_ok=True)
            s = height / 1080
            box = (round(image.width / 2 - 140 * s), round(100 * s), round(image.width / 2 + 140 * s), round(265 * s))
            image.crop(box).save(os.path.join(folder, time.strftime("%Y%m%d-%H%M%S-") + (element or "unsure") + ".png"))
            names = sorted(os.listdir(folder))
            for old in names[:-40]:   # keep the last 40
                os.remove(os.path.join(folder, old))
        except OSError as e:
            log(f"Couldn't save the dragon banner: {e}", "warn")
    return element, text
