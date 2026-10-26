"""The webcam's own settings, straight from its Windows driver (DirectShow).

The cam page sets most of a webcam's picture through the browser (white
balance, exposure, brightness, zoom...); the browser has no way to set the
rest: gain (a webcam's "ISO"), backlight compensation, hue, gamma and
anti-flicker. Player Cam sets those here, on the same camera, through the
driver's IAMVideoProcAmp / IAMCameraControl, the settings Windows' own
camera property pages use. Windows only; elsewhere there are none.

    props(label)            -> {name: {"min", "max", "step", "default",
                                       "value", "auto"}}   (auto: None when
                                       the camera has no automatic mode)
    set_props(label, values) values {name: {"value": n, "auto": bool}}

label is the browser's name for the camera, e.g. "Logi C615 HD WebCam
(046d:082c)": the camera is found by its USB ids and name.
"""

import ctypes
import re
import sys
import threading

# IAMVideoProcAmp properties, and IAMCameraControl's (VideoProcAmp_*, CameraControl_*).
PROC_AMP = {"brightness": 0, "contrast": 1, "hue": 2, "saturation": 3, "sharpness": 4, "gamma": 5,
            "whitebalance": 7, "backlight": 8, "gain": 9, "powerline": 13}
CAMERA = {"pan": 0, "tilt": 1, "zoom": 3, "exposure": 4, "focus": 6}
AUTO, MANUAL = 1, 2

_lock = threading.Lock()   # one driver conversation at a time

if sys.platform == "win32":
    from ctypes import POINTER, byref, c_long, c_ulong, c_void_p, c_wchar_p, wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]

    class VARIANT(ctypes.Structure):
        _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                    ("val", c_void_p), ("pad", c_void_p)]

    _ole32 = ctypes.OleDLL("ole32")
    _oleaut32 = ctypes.WinDLL("oleaut32")
    _oleaut32.VariantClear.argtypes = [POINTER(VARIANT)]

    def _guid(text):
        g = GUID()
        _ole32.CLSIDFromString(c_wchar_p(text), byref(g))
        return g

    CLSID_SystemDeviceEnum = _guid("{62BE5D10-60EB-11d0-BD3B-00A0C911CE86}")
    CLSID_VideoInputDeviceCategory = _guid("{860BB310-5D01-11d0-BD3B-00A0C911CE86}")
    IID_ICreateDevEnum = _guid("{29840822-5B84-11D0-BD3B-00A0C911CE86}")
    IID_IPropertyBag = _guid("{55272A00-42CB-11CE-8135-00AA004BB851}")
    IID_IBaseFilter = _guid("{56A86895-0AD4-11CE-B03A-0020AF0BA770}")
    IID_IAMVideoProcAmp = _guid("{C6E13360-30AC-11d0-A18C-00A0C9118956}")
    IID_IAMCameraControl = _guid("{C6E13370-30AC-11d0-A18C-00A0C9118956}")

    def _call(ptr, index, *args, argtypes=None):
        """Call method `index` of the COM object at ptr; returns the HRESULT."""
        vtbl = ctypes.cast(c_void_p(ptr), POINTER(POINTER(c_void_p))).contents
        proto = ctypes.WINFUNCTYPE(c_long, c_void_p, *(argtypes or [type(a) for a in args]))
        return proto(vtbl[index])(ptr, *args)

    def _release(ptr):
        if ptr:
            _call(ptr, 2, argtypes=[])

    def _query(ptr, iid):
        out = c_void_p()
        hr = _call(ptr, 0, byref(iid), byref(out), argtypes=[c_void_p, c_void_p])
        return out.value if hr >= 0 else None

    def _read(bag, name):
        v = VARIANT()
        if _call(bag, 3, c_wchar_p(name), byref(v), None, argtypes=[c_wchar_p, c_void_p, c_void_p]) < 0:
            return ""
        text = ctypes.wstring_at(v.val) if v.vt == 8 and v.val else ""   # VT_BSTR
        _oleaut32.VariantClear(byref(v))
        return text

    def _devices():
        """[(friendly name, device path, moniker)] of the video inputs; release the monikers."""
        try:
            _ole32.CoInitializeEx(None, 0)
        except OSError:
            pass   # already set up on this thread
        dev_enum = c_void_p()
        _ole32.CoCreateInstance(byref(CLSID_SystemDeviceEnum), None, 1, byref(IID_ICreateDevEnum), byref(dev_enum))
        out = []
        try:
            enum = c_void_p()
            hr = _call(dev_enum.value, 3, byref(CLSID_VideoInputDeviceCategory), byref(enum), wintypes.DWORD(0),
                       argtypes=[c_void_p, c_void_p, wintypes.DWORD])
            if hr != 0 or not enum.value:
                return out
            try:
                while True:
                    moniker, fetched = c_void_p(), c_ulong()
                    if _call(enum.value, 3, c_ulong(1), byref(moniker), byref(fetched),
                             argtypes=[c_ulong, c_void_p, c_void_p]) != 0 or not fetched.value:
                        break
                    bag = c_void_p()
                    # IMoniker::BindToStorage
                    if _call(moniker.value, 9, None, None, byref(IID_IPropertyBag), byref(bag),
                             argtypes=[c_void_p, c_void_p, c_void_p, c_void_p]) >= 0 and bag.value:
                        name, path = _read(bag.value, "FriendlyName"), _read(bag.value, "DevicePath")
                        _release(bag.value)
                    else:
                        name, path = "", ""
                    out.append((name, path, moniker.value))
            finally:
                _release(enum.value)
        finally:
            _release(dev_enum.value)
        return out

    def _open(label):
        """The camera's (IAMVideoProcAmp, IAMCameraControl), either may be None."""
        devices = _devices()
        try:
            ids = re.search(r"\(([0-9a-f]{4}):([0-9a-f]{4})\)\s*$", label or "", re.I)
            name = re.sub(r"\s*\([0-9a-f]{4}:[0-9a-f]{4}\)\s*$", "", label or "", flags=re.I).strip().lower()

            def score(d):
                s = 0
                if ids and f"vid_{ids.group(1)}&pid_{ids.group(2)}".lower() in d[1].lower():
                    s += 2
                if name and d[0].strip().lower() == name:
                    s += 1
                return s
            ranked = sorted(devices, key=score, reverse=True)
            if not ranked or (score(ranked[0]) == 0 and len(ranked) > 1):
                return None, None
            moniker = ranked[0][2]
            base = c_void_p()
            # IMoniker::BindToObject
            if _call(moniker, 8, None, None, byref(IID_IBaseFilter), byref(base),
                     argtypes=[c_void_p, c_void_p, c_void_p, c_void_p]) < 0 or not base.value:
                return None, None
            amp, cam = _query(base.value, IID_IAMVideoProcAmp), _query(base.value, IID_IAMCameraControl)
            _release(base.value)
            return amp, cam
        finally:
            for d in devices:
                _release(d[2])

    def _range(obj, prop):
        lo, hi, step, default, caps = c_long(), c_long(), c_long(), c_long(), c_long()
        if _call(obj, 3, c_long(prop), byref(lo), byref(hi), byref(step), byref(default), byref(caps),
                 argtypes=[c_long] + [c_void_p] * 5) < 0 or hi.value <= lo.value:
            return None
        value, flags = c_long(), c_long()
        if _call(obj, 5, c_long(prop), byref(value), byref(flags), argtypes=[c_long, c_void_p, c_void_p]) < 0:
            return None
        return {"min": lo.value, "max": hi.value, "step": max(1, step.value), "default": default.value,
                "value": value.value, "auto": bool(flags.value & AUTO) if caps.value & AUTO else None}


def props(label):
    if sys.platform != "win32":
        return {}
    with _lock:
        amp, cam = _open(label)
        out = {}
        try:
            for obj, table in ((amp, PROC_AMP), (cam, CAMERA)):
                if obj:
                    for name, prop in table.items():
                        r = _range(obj, prop)
                        if r:
                            out[name] = r
        finally:
            _release(amp)
            _release(cam)
        return out


def set_props(label, values):
    """Set some of the camera's settings; returns props(label) after."""
    if sys.platform != "win32":
        return {}
    with _lock:
        amp, cam = _open(label)
        try:
            for name, want in (values or {}).items():
                obj, prop = (amp, PROC_AMP[name]) if name in PROC_AMP else (cam, CAMERA.get(name))
                if not obj or prop is None or not isinstance(want, dict):
                    continue
                now = _range(obj, prop)
                if not now:
                    continue
                auto = bool(want.get("auto")) and now["auto"] is not None
                value = want.get("value", now["value"])
                value = int(min(now["max"], max(now["min"], round(float(value)))))
                _call(obj, 4, c_long(prop), c_long(value), c_long(AUTO if auto else MANUAL),
                      argtypes=[c_long, c_long, c_long])
        finally:
            _release(amp)
            _release(cam)
    return props(label)
