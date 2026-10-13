"""Split the PSD's HUD BAR layer into a plain bar and a recolourable line layer.

Reads design/hud_bar.png (the HUD BAR layer exported from the PSD). The
coloured contour lines in the bar are baked into the same image as the
black background and white icons. This separates them so the overlay can tint
the lines with each team's colour:

    web/assets/hud_base.png   the bar with the lines removed
    web/assets/hud_lines.png  the lines alone, white, strength in the alpha
    web/assets/grub_*.png     the void grub icons from the lower strip

Run from the project folder:  python tools/split_bar.py   (needs Pillow)
"""

import os

from PIL import Image, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "web", "assets")


def main():
    bar = Image.open(os.path.join(ROOT, "design", "hud_bar.png")).convert("RGBA")
    base = bar.copy()
    lines = Image.new("RGBA", bar.size, (255, 255, 255, 0))
    src, out_base, out_lines = bar.load(), base.load(), lines.load()
    # The icons are bright white; mask them plus a few pixels around them so
    # their dark detail and edges stay in the base.
    icons = Image.new("L", bar.size, 0)
    ic = icons.load()
    for y in range(bar.height):
        for x in range(bar.width):
            r, g, b, a = src[x, y]
            if min(r, g, b) >= 150 and a > 0:
                ic[x, y] = 255
    icons = icons.filter(ImageFilter.MaxFilter(7)).load()
    for y in range(bar.height):
        for x in range(bar.width):
            r, g, b, a = src[x, y]
            hi, lo = max(r, g, b), min(r, g, b)
            # Lines are coloured (or faint); the background is black and the
            # icons bright white.
            if hi == 0:
                continue
            coloured = (hi - lo) / hi >= 0.25
            if not coloured and (icons[x, y] or hi >= 90):
                continue
            # Boosted, since the tint colour is darker than the white mask.
            out_lines[x, y] = (255, 255, 255, min(255, round(hi * a / 255 * 1.6)))
            out_base[x, y] = (0, 0, 0, a)
    # The void grub icons in the lower strip become their own images, so the
    # counters can be switched off: cut them out and repaint the strip, which
    # only changes from top to bottom, from 30px to the side.
    bp = base.load()
    for side, (x0, x1) in {"left": (199, 221), "right": (678, 700)}.items():
        icon = Image.new("RGBA", (x1 - x0, 23), (255, 255, 255, 0))
        ip = icon.load()
        for y in range(75, 98):
            for x in range(x0, x1):
                r, g, b, a = bp[x, y]
                ip[x - x0, y - 75] = (255, 255, 255, round(min(r, g, b) * a / 255))
                bp[x, y] = bp[x - 30 if side == "left" else x + 30, y]
        icon.save(os.path.join(ASSETS, f"grub_{side}.png"))
    base.save(os.path.join(ASSETS, "hud_base.png"))
    lines.save(os.path.join(ASSETS, "hud_lines.png"))


if __name__ == "__main__":
    main()
