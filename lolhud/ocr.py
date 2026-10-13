"""Reads team gold off the spectator top bar on screen.

League's game data doesn't include other players' gold, but the spectator
HUD shows each team's total ("8.0k") at the top of the screen. This captures
that part of the League window a couple of times a second and reads the
numbers with Windows' built-in text recognition (Windows.Media.Ocr), so
there's nothing extra to install.

Windows only; elsewhere (or if anything goes wrong) the HUD falls back to
its gold estimate.

    python -m lolhud.ocr screenshot.png    read a saved 1920x1080 screenshot
"""

import asyncio
import io
import re
import sys
import threading
import time

from .log import log

# Where the two gold numbers are on a 1920x1080 spectator screen: x, y, w, h.
# They scale with the window's height and stay centred, like the top bar.
REF_HEIGHT = 1080
DEFAULT_BOXES = {"blue": [804, 16, 54, 24], "red": [1106, 16, 54, 24]}
WINDOW_TITLE = "League of Legends (TM) Client"
WINDOW_CLASS = "RiotWindowClass"

_NUMBER = re.compile(r"(\d{1,3})(?:[.,](\d))?\s*([kK])?")
# Letters OCR sometimes returns for these digits.
_FIXES = str.maketrans({"O": "0", "o": "0", "D": "0", "Q": "0", "l": "1", "I": "1", "|": "1", "i": "1",
                        "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2", "g": "9", "q": "9"})


def parse_gold(text):
    """"8.0k" -> 8000, "12.4k" -> 12400. None if it doesn't look like gold."""
    if not text:
        return None
    cleaned = text.strip().replace(" ", "")
    # Keep a trailing k/K, translate look-alike letters everywhere else.
    k = cleaned[-1:] if cleaned[-1:] in "kK" else ""
    body = (cleaned[:-1] if k else cleaned).translate(_FIXES)
    m = _NUMBER.search(body + k)
    if not m:
        return None
    whole, tenth, has_k = m.group(1), m.group(2), m.group(3)
    if tenth is None and not has_k:
        return None  # a bare number isn't the "8.0k" format; probably junk
    return int(whole) * 1000 + int(tenth or 0) * 100


def scaled_box(box, width, height):
    """Map a 1920x1080 box onto a window of this size (centred, scaled by height)."""
    x, y, w, h = box
    s = height / REF_HEIGHT
    left = width / 2 + (x - 960) * s
    return (round(left), round(y * s), round(left + w * s), round((y + h) * s))


# Ways to prepare a number for text recognition, tried in order until one
# reads as gold. Windows OCR is fussy about single short words, so having a
# couple of alternatives makes a big difference: (scale, sharp edges?, border).
VARIANTS = [(4, False, 24), (4, True, 24), (3, False, 48), (6, True, 32)]


def prepare(crop, variant=0):
    """Bright coloured text on dark teal -> large black text on white."""
    from PIL import Image, ImageChops, ImageOps
    scale, sharp, border = VARIANTS[variant]
    r, g, b = crop.convert("RGB").split()
    # Brightest channel: the blue or red digits stand out, the teal bar doesn't.
    gray = ImageChops.lighter(ImageChops.lighter(r, g), b)
    gray = ImageOps.autocontrast(gray, cutoff=2)
    big = gray.resize((gray.width * scale, gray.height * scale), Image.LANCZOS)
    if sharp:
        big = big.point(lambda v: 0 if v > 110 else 255)  # black text on white
    else:
        big = ImageOps.invert(big)  # keep smooth edges
    return ImageOps.expand(big, border=border, fill=255)


class WindowsOcr:
    """Windows.Media.Ocr through the winrt packages."""

    def __init__(self):
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            engine = OcrEngine.try_create_from_language(Language("en-US"))
        if engine is None:
            raise RuntimeError("Windows text recognition isn't installed for any of your languages")
        self.engine = engine

    def read(self, image):
        from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import DataWriter
        rgba = image.convert("RGBA")
        writer = DataWriter()
        writer.write_bytes(rgba.tobytes("raw", "BGRA"))
        buffer = writer.detach_buffer()
        try:
            bitmap = SoftwareBitmap.create_copy_from_buffer(buffer, BitmapPixelFormat.BGRA8, rgba.width, rgba.height)
        except TypeError:  # older winrt wrappers
            bitmap = SoftwareBitmap(BitmapPixelFormat.BGRA8, rgba.width, rgba.height, BitmapAlphaMode.PREMULTIPLIED)
            bitmap.copy_from_buffer(buffer)

        async def run():
            return await self.engine.recognize_async(bitmap)

        return asyncio.run(run()).text


# --- capturing the League window -------------------------------------------

def make_dpi_aware():
    """Use real pixels, so Windows display scaling (125%, 150%...) doesn't
    shift where the capture lands. Must run before any window is created."""
    import ctypes
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()


def _win32():
    import ctypes
    from ctypes import wintypes
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    H = ctypes.c_void_p
    for fn, res, args in [
        (user32.FindWindowW, H, [wintypes.LPCWSTR, wintypes.LPCWSTR]),
        (user32.IsIconic, wintypes.BOOL, [H]),
        (user32.GetClientRect, wintypes.BOOL, [H, ctypes.POINTER(wintypes.RECT)]),
        (user32.ClientToScreen, wintypes.BOOL, [H, ctypes.POINTER(wintypes.POINT)]),
        (user32.GetDC, H, [H]),
        (user32.ReleaseDC, ctypes.c_int, [H, H]),
        (user32.PrintWindow, wintypes.BOOL, [H, H, wintypes.UINT]),
        (gdi32.CreateCompatibleDC, H, [H]),
        (gdi32.CreateCompatibleBitmap, H, [H, ctypes.c_int, ctypes.c_int]),
        (gdi32.SelectObject, H, [H, H]),
        (gdi32.BitBlt, wintypes.BOOL, [H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       H, ctypes.c_int, ctypes.c_int, wintypes.DWORD]),
        (gdi32.GetDIBits, ctypes.c_int, [H, H, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
                                         wintypes.UINT]),
        (gdi32.DeleteObject, wintypes.BOOL, [H]),
        (gdi32.DeleteDC, wintypes.BOOL, [H]),
    ]:
        fn.restype, fn.argtypes = res, args
    return ctypes, wintypes, user32, gdi32


def _bitmap_to_image(ctypes, gdi32, mdc, bmp, width, height):
    from PIL import Image

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
                    ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16),
                    ("biCompression", ctypes.c_uint32), ("biSizeImage", ctypes.c_uint32),
                    ("biXPelsPerMeter", ctypes.c_int32), ("biYPelsPerMeter", ctypes.c_int32),
                    ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]

    info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mdc, bmp, 0, height, buf, ctypes.byref(info), 0)
    return Image.frombuffer("RGB", (width, height), buf.raw, "raw", "BGRX", 0, 1)


def capture_league():
    """The League window's client area as an image, or (None, reason)."""
    ctypes, wintypes, user32, gdi32 = _win32()
    hwnd = user32.FindWindowW(None, WINDOW_TITLE) or user32.FindWindowW(WINDOW_CLASS, None)
    if not hwnd:
        return None, "League window not found"
    if user32.IsIconic(hwnd):
        return None, "League window is minimised"
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    width, height = rect.right, rect.bottom
    if width < 100 or height < 100:
        return None, "League window is too small"
    # Only the top bar is needed.
    strip = max(1, int(height * 0.05) + 2)

    screen = user32.GetDC(None)
    mdc = gdi32.CreateCompatibleDC(screen)
    try:
        # 1) Ask the window to draw itself (works even if other windows cover it).
        full = gdi32.CreateCompatibleBitmap(screen, width, height)
        old = gdi32.SelectObject(mdc, full)
        image = None
        if user32.PrintWindow(hwnd, mdc, 3):  # PW_CLIENTONLY | PW_RENDERFULLCONTENT
            image = _bitmap_to_image(ctypes, gdi32, mdc, full, width, strip)
            if max(image.convert("L").getextrema()) < 16:
                image = None  # came back black: some fullscreen modes don't support it
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(full)
        # 2) Otherwise copy it from the screen (the top bar must be visible).
        if image is None:
            point = wintypes.POINT(0, 0)
            user32.ClientToScreen(hwnd, ctypes.byref(point))
            bmp = gdi32.CreateCompatibleBitmap(screen, width, strip)
            old = gdi32.SelectObject(mdc, bmp)
            gdi32.BitBlt(mdc, 0, 0, width, strip, screen, point.x, point.y, 0x00CC0020 | 0x40000000)
            image = _bitmap_to_image(ctypes, gdi32, mdc, bmp, width, strip)
            gdi32.SelectObject(mdc, old)
            gdi32.DeleteObject(bmp)
    finally:
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(None, screen)
    return (image, height), None


def read_image(image, ocr, height=None, boxes=None):
    """Read both teams' gold from a capture of the top of the League window."""
    height = height or image.height
    boxes = boxes or DEFAULT_BOXES
    out = {}
    for team, box in boxes.items():
        crop = image.crop(scaled_box(box, image.width, height))
        for variant in range(len(VARIANTS)):
            ready = prepare(crop, variant)
            text = ocr.read(ready)
            value = parse_gold(text)
            if value is not None:
                break
        out[team] = {"text": text, "value": value, "image": ready, "variant": variant}
    return out


class ScreenGold:
    """Keeps reading the gold in the background and keeps the last good values."""

    STALE_AFTER = 5.0  # seconds without a good read before the estimate takes over

    def __init__(self, config):
        self.config = config
        self.values = {"blue": None, "red": None}
        self.read_at = 0.0
        self.status = "starting"
        self.ocr = None
        self._pending = {"blue": None, "red": None}
        self._stop = threading.Event()
        self.available, self.reason = self._check()

    def _check(self):
        if sys.platform != "win32":
            return False, "Reading the screen only works on Windows"
        make_dpi_aware()
        try:
            import PIL  # noqa: F401
            self.ocr = WindowsOcr()
        except Exception as e:
            return False, f"Windows text recognition unavailable: {e}"
        return True, ""

    def start(self):
        if self.available:
            threading.Thread(target=self._run, daemon=True).start()
        else:
            self.status = self.reason
            log(f"Screen gold off: {self.reason}", "warn")

    def stop(self):
        self._stop.set()

    def fresh(self):
        """Last good values ({"blue": 8000, "red": 8900}) if recent enough, else None."""
        if time.time() - self.read_at > self.STALE_AFTER:
            return None
        if None in self.values.values():
            return None
        return dict(self.values)

    def reset(self):
        self.values = {"blue": None, "red": None}
        self._pending = {"blue": None, "red": None}

    def _accept(self, team, value):
        """Team gold only goes up, and not by huge jumps. A surprising value is
        taken only once it's been read twice in a row (e.g. a new game)."""
        prev = self.values[team]
        if value is None:
            return False
        if prev is None or prev <= value <= prev + 3000:
            self.values[team], self._pending[team] = value, None
            return True
        if self._pending[team] == value:
            self.values[team], self._pending[team] = value, None
            return True
        self._pending[team] = value
        return False

    def read_once(self):
        captured, reason = capture_league()
        if not captured:
            return None, reason
        image, height = captured
        result = read_image(image, self.ocr, height, self.config.get().get("gold_ocr_boxes"))
        return result, None

    def _run(self):
        last_reason = None
        while not self._stop.is_set():
            if self.config.get().get("gold_mode") == "screen":
                try:
                    result, reason = self.read_once()
                    if result:
                        ok = [self._accept(t, r["value"]) for t, r in result.items()]
                        if all(ok):
                            self.read_at = time.time()
                            reason = None
                        else:
                            reason = "Couldn't read the gold on screen: " + ", ".join(
                                f"{t} {r['text']!r}" for t, r in result.items())
                except Exception as e:
                    reason = f"Screen read failed: {e!r}"
                self.status = reason or "reading"
                if reason != last_reason and reason:
                    log(reason, "warn")
                elif reason != last_reason:
                    log("Reading team gold from the spectator top bar")
                last_reason = reason
            self._stop.wait(0.5)

    def test(self):
        """One read for the control panel's Test button, with the images it read."""
        import base64
        if not self.available:
            return {"ok": False, "error": self.reason}
        try:
            result, reason = self.read_once()
        except Exception as e:
            return {"ok": False, "error": repr(e)}
        if not result:
            return {"ok": False, "error": reason}
        out = {"ok": all(r["value"] is not None for r in result.values())}
        for team, r in result.items():
            buf = io.BytesIO()
            r["image"].save(buf, "PNG")
            out[team] = {"text": r["text"], "value": r["value"],
                         "image": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}
        return out


def main(argv):
    """Read a saved screenshot: python -m lolhud.ocr screenshot.png [expected_blue expected_red]"""
    from PIL import Image
    image = Image.open(argv[0])
    height = int(argv[3]) if len(argv) > 3 else None
    result = read_image(image, WindowsOcr(), height)
    for team, r in result.items():
        print(f"{team}: text={r['text']!r} value={r['value']} (variant {r['variant']})")
    if len(argv) >= 3:
        expected = {"blue": int(argv[1]), "red": int(argv[2])}
        if any(result[t]["value"] != v for t, v in expected.items()):
            print("MISMATCH")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
