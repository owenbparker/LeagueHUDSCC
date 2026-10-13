"""HUD profiles: each is a folder with a profile.json listing what to draw.

Built-in profiles ship in web/profiles/<id>/; imported ones (from a PSD)
live in profiles/<id>/ next to the app and can be deleted. Both are served
at /profiles/<id>/... for the overlay.
"""

import json
import os
import re
import shutil

from .config import PROFILE_DIR, WEB_DIR

BUILTIN_DIR = os.path.join(WEB_DIR, "profiles")
DEFAULT_PROFILE = "scc-2026"
_version = 0  # bumped when profiles change, so open overlays reload


def version():
    return _version


def changed():
    global _version
    _version += 1


def _folders():
    for builtin, root in ((True, BUILTIN_DIR), (False, PROFILE_DIR)):
        if os.path.isdir(root):
            for pid in sorted(os.listdir(root)):
                if os.path.isfile(os.path.join(root, pid, "profile.json")):
                    yield pid, builtin, os.path.join(root, pid)


def folder(pid):
    """The profile's folder (imported ones take precedence), or None."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", pid or ""):
        return None
    for candidate in (os.path.join(PROFILE_DIR, pid), os.path.join(BUILTIN_DIR, pid)):
        if os.path.isfile(os.path.join(candidate, "profile.json")):
            return candidate
    return None


def load(pid):
    path = folder(pid)
    if not path:
        return None
    with open(os.path.join(path, "profile.json"), encoding="utf-8") as f:
        return json.load(f)


def save(pid, data):
    path = os.path.join(PROFILE_DIR, pid)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "profile.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    changed()


def listing():
    seen, out = set(), []
    for pid, builtin, path in _folders():
        if pid in seen:
            continue
        seen.add(pid)
        try:
            with open(os.path.join(path, "profile.json"), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        out.append({"id": pid, "name": data.get("name", pid), "builtin": builtin,
                    "missing_fonts": data.get("missing_fonts", []), "report": data.get("import_report")})
    return out


def new_id(name):
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "hud").lower()).strip("-") or "hud"
    pid, n = slug, 2
    while folder(pid):
        pid, n = f"{slug}-{n}", n + 1
    return pid


def delete(pid):
    path = os.path.join(PROFILE_DIR, pid)
    if not folder(pid) or not os.path.isdir(path):
        raise ValueError("Built-in profiles can't be deleted")
    shutil.rmtree(path)
    changed()
