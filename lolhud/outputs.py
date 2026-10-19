"""Writes the current HUD fields to files that vMix / GT Title Designer can read."""

import csv
import io
import json
import os
import re
import threading
from xml.sax.saxutils import escape


def to_csv(fields):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(fields.keys())
    writer.writerow(fields.values())
    return buf.getvalue()


def xml_tag(name):
    tag = re.sub(r"[^A-Za-z0-9_]", "_", name)
    return tag if tag[:1].isalpha() else "F_" + tag


def to_xml(fields):
    rows = "".join(f"<{xml_tag(k)}>{escape(str(v))}</{xml_tag(k)}>" for k, v in fields.items())
    return f'<?xml version="1.0" encoding="utf-8"?>\n<hud><row>{rows}</row></hud>\n'


def _write(path, text):
    # Write to a temp file then swap it in, so vMix never reads half a file.
    # If vMix has the file locked (Windows), fall back to writing in place.
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    try:
        os.replace(tmp, path)
    except PermissionError:
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        except PermissionError:
            pass
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


class FileWriter:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self._last = None
        # The poller and the control panel both refresh: one write at a time
        # (they share the .tmp files).
        self._lock = threading.Lock()

    def write(self, fields):
        with self._lock:
            if fields == self._last:
                return
            self._last = dict(fields)
            _write(os.path.join(self.out_dir, "hud.csv"), to_csv(fields))
            _write(os.path.join(self.out_dir, "hud.json"), json.dumps([fields], indent=2))
            _write(os.path.join(self.out_dir, "hud.xml"), to_xml(fields))
