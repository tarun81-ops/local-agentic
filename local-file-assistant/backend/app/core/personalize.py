"""Every user-editable preference that shapes answers, in one place.

Stored under one key ("personalize") in settings.json, so it lives next to the other prefs and
is wiped with them. Later features (folder profiles, appearance, performance) reserve their key
here now and tighten their validation when they land."""
import os
import re
from pathlib import Path

from app.core import prefs

KEY = "personalize"
PROFILE_MAX = 800
STYLES = ("concise", "detailed", "study", "simple")
LANGUAGES = ("auto", "english", "hinglish")
VERIFIER = ("normal", "strict")
THEMES = ("system", "light", "dark")
ACCENTS = ("red", "blue", "green", "violet")
OVERLAY_POSITIONS = ("cursor", "center", "top-right")
FONT_SCALE = (0.9, 1.3)
APPEARANCE = {"theme": "system", "accent": "red", "font_scale": 1.0, "overlay_compact": False, "overlay_position": "top-right"}

DEFAULTS: dict = {
    "profile": "",
    "answer_style": "concise",
    "language": "auto",
    "cite_pages": True,
    "verifier_strict": "normal",
    # reserved: validated loosely until their phase tightens them
    "perf_profile": "balanced",
    "appearance": dict(APPEARANCE),
    "memory_review": True,
    "folder_profiles": {},
    "inbox_folders": [],
}

MAX_EXTENSIONS, MAX_GLOBS = 30, 30


def norm_path(p: str | Path) -> str:
    """The key a folder is stored and compared under: resolved, and case-folded the way Windows
    compares paths (sqlite_fts.root_for relies on the same rule via Path.relative_to)."""
    return os.path.normcase(str(Path(p).expanduser().resolve()))


def clean_folder_profile(raw) -> dict:
    """Validates one folder's settings; raises ValueError with a message for the user."""
    if not isinstance(raw, dict):
        raise ValueError("Folder settings have the wrong shape.")
    label = raw.get("label", "")
    style = raw.get("answer_style", "")
    exts, globs = raw.get("extensions", []), raw.get("exclude_globs", [])
    if not isinstance(label, str) or len(label.strip()) > 60:
        raise ValueError("A folder label can be at most 60 characters.")
    if style not in ("", *STYLES):
        raise ValueError(f"Folder answer style must be one of: {', '.join(STYLES)}.")
    if not isinstance(exts, list) or len(exts) > MAX_EXTENSIONS:
        raise ValueError(f"File types must be a list of at most {MAX_EXTENSIONS}.")
    if not isinstance(globs, list) or len(globs) > MAX_GLOBS or not all(isinstance(g, str) and 0 < len(g.strip()) <= 120 for g in globs):
        raise ValueError(f"Exclusions must be a list of at most {MAX_GLOBS} patterns, each up to 120 characters.")
    clean_exts = []
    for e in exts:
        e = str(e).strip().lower().lstrip("*")
        e = e if e.startswith(".") else "." + e
        if not re.fullmatch(r"\.[a-z0-9]{1,8}", e):
            raise ValueError(f"“{e}” isn’t a file type like .pdf")
        if e not in clean_exts:
            clean_exts.append(e)
    caption = raw.get("caption_images")
    if caption is not None and not isinstance(caption, bool):
        raise ValueError("caption_images must be on, off or unset.")
    if not isinstance(raw.get("ocr_only", False), bool):
        raise ValueError("ocr_only must be on or off.")
    return {
        "label": label.strip(),
        "answer_style": style,
        "extensions": clean_exts,
        "exclude_globs": [g.strip() for g in globs],
        "caption_images": caption,
        "ocr_only": bool(raw.get("ocr_only", False)),
    }


def clean_appearance(raw) -> dict:
    """Validates the appearance settings (a partial dict is fine; unknown keys are not)."""
    if not isinstance(raw, dict) or set(raw) - set(APPEARANCE):
        raise ValueError("appearance has the wrong shape.")
    out = {**APPEARANCE, **raw}
    for key, allowed in (("theme", THEMES), ("accent", ACCENTS), ("overlay_position", OVERLAY_POSITIONS)):
        if out[key] not in allowed:
            raise ValueError(f"{key} must be one of: {', '.join(allowed)}.")
    scale = out["font_scale"]
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not FONT_SCALE[0] <= scale <= FONT_SCALE[1]:
        raise ValueError(f"font_scale must be a number from {FONT_SCALE[0]} to {FONT_SCALE[1]}.")
    if not isinstance(out["overlay_compact"], bool):
        raise ValueError("overlay_compact must be on or off.")
    out["font_scale"] = round(float(scale), 2)
    return out


def clean_folder_profiles(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("folder_profiles has the wrong shape.")
    return {norm_path(k): clean_folder_profile(v) for k, v in raw.items()}


def folder_profile(root: str | Path) -> dict:
    """This folder's settings, or {} when it has none (case-insensitive on Windows)."""
    return get_all()["folder_profiles"].get(norm_path(root), {})


def set_folder_profile(root: str | Path, profile: dict) -> dict:
    clean = clean_folder_profile(profile)
    profiles = {**get_all()["folder_profiles"], norm_path(root): clean}
    update({"folder_profiles": profiles})
    return clean


_CHOICES = {"answer_style": STYLES, "language": LANGUAGES, "verifier_strict": VERIFIER}
_RESERVED_TYPES = {"appearance": dict, "folder_profiles": dict, "inbox_folders": list}


def get_all() -> dict:
    """Saved values over the defaults. A damaged or hand-edited entry falls back to the default."""
    stored = prefs.get(KEY, {})
    stored = stored if isinstance(stored, dict) else {}
    out = {k: stored.get(k, v) for k, v in DEFAULTS.items()}
    out["appearance"] = {**APPEARANCE, **(out["appearance"] if isinstance(out["appearance"], dict) else {})}
    return out


def _check(key: str, value):
    if key not in DEFAULTS:
        raise ValueError(f"Unknown setting: {key}")
    if key == "profile":
        if not isinstance(value, str):
            raise ValueError("About me must be text.")
        value = value.strip()
        if len(value) > PROFILE_MAX:
            raise ValueError(f"About me can be at most {PROFILE_MAX} characters (yours is {len(value)}).")
    elif key in _CHOICES:
        if value not in _CHOICES[key]:
            raise ValueError(f"{key} must be one of: {', '.join(_CHOICES[key])}.")
    elif isinstance(DEFAULTS[key], bool):
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be on or off.")
    elif key == "appearance":
        value = clean_appearance(value)
    elif key == "folder_profiles":
        value = clean_folder_profiles(value)
    elif key in _RESERVED_TYPES:
        if not isinstance(value, _RESERVED_TYPES[key]):
            raise ValueError(f"{key} has the wrong shape.")
    elif not isinstance(value, str):
        raise ValueError(f"{key} must be text.")
    return value


def update(patch: dict) -> dict:
    """Partial update: validates every key first, so a bad value changes nothing."""
    if isinstance(patch.get("appearance"), dict):  # a partial appearance change keeps the rest
        patch = {**patch, "appearance": {**get_all()["appearance"], **patch["appearance"]}}
    clean = {k: _check(k, v) for k, v in patch.items()}
    merged = {**get_all(), **clean}
    prefs.set(KEY, merged)
    return merged


def profile() -> str:
    return get_all()["profile"]


def style() -> tuple[str, str, bool]:
    s = get_all()
    return s["answer_style"], s["language"], s["cite_pages"]
