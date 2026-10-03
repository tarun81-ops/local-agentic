"""Every user-editable preference that shapes answers, in one place.

Stored under one key ("personalize") in settings.json, so it lives next to the other prefs and
is wiped with them. Later features (folder profiles, appearance, performance) reserve their key
here now and tighten their validation when they land."""
from app.core import prefs

KEY = "personalize"
PROFILE_MAX = 800
STYLES = ("concise", "detailed", "study", "simple")
LANGUAGES = ("auto", "english", "hinglish")
VERIFIER = ("normal", "strict")

DEFAULTS: dict = {
    "profile": "",
    "answer_style": "concise",
    "language": "auto",
    "cite_pages": True,
    "verifier_strict": "normal",
    # reserved: validated loosely until their phase tightens them
    "perf_profile": "balanced",
    "appearance": {},
    "memory_review": True,
    "folder_profiles": {},
    "inbox_folders": [],
}

_CHOICES = {"answer_style": STYLES, "language": LANGUAGES, "verifier_strict": VERIFIER}
_RESERVED_TYPES = {"appearance": dict, "folder_profiles": dict, "inbox_folders": list}


def get_all() -> dict:
    """Saved values over the defaults. A damaged or hand-edited entry falls back to the default."""
    stored = prefs.get(KEY, {})
    stored = stored if isinstance(stored, dict) else {}
    return {k: stored.get(k, v) for k, v in DEFAULTS.items()}


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
    elif key in _RESERVED_TYPES:
        if not isinstance(value, _RESERVED_TYPES[key]):
            raise ValueError(f"{key} has the wrong shape.")
    elif not isinstance(value, str):
        raise ValueError(f"{key} must be text.")
    return value


def update(patch: dict) -> dict:
    """Partial update: validates every key first, so a bad value changes nothing."""
    clean = {k: _check(k, v) for k, v in patch.items()}
    merged = {**get_all(), **clean}
    prefs.set(KEY, merged)
    return merged


def profile() -> str:
    return get_all()["profile"]


def style() -> tuple[str, str, bool]:
    s = get_all()
    return s["answer_style"], s["language"], s["cite_pages"]
