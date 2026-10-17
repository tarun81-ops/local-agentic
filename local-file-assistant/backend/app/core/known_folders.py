"""Finds the user's everyday folders (Documents, Desktop, Downloads, Pictures) and adds
them to the index without a folder picker, so the app has access to the files on this PC from the
first launch. Only these named folders are touched, never a whole drive: the rest of the PC
(AppData, Program Files, other users) stays out of the index unless the user adds it by hand.
Which ones were already offered is remembered, so a folder removed on the Index page stays removed."""
import ctypes
import logging
import os
import sys
import uuid
from ctypes import wintypes
from pathlib import Path

from app.config import settings
from app.core import prefs

log = logging.getLogger(__name__)

# key -> (label, Windows Known Folder id, fallback folder name under the user's home, on by default)
COMMON = {
    "documents": ("Documents", "FDD39AD0-238F-46AF-ADB4-6C85480369C7", "Documents", True),
    "desktop": ("Desktop", "B4BFCC3A-DB2C-424C-B029-7FE99A87C641", "Desktop", True),
    "downloads": ("Downloads", "374DE290-123F-4565-9164-39C4925E467B", "Downloads", True),
    # Off by default: photo folders are big, and scanning them with OCR is slow on a CPU.
    "pictures": ("Pictures", "33E28130-4E1E-4676-835A-98395C3BC3BB", "Pictures", False),
}

# What every auto-added folder skips: dependency and build folders hold thousands of files nobody asks about.
DEFAULT_EXCLUDES = ["node_modules", ".git", "__pycache__", ".venv", "venv", "site-packages", "*.tmp", "~$*"]

PREF_ENABLED = "auto_access"          # master switch, on by default
PREF_DONE = "auto_access_offered"      # keys already added once; never re-added behind the user's back


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


def _windows_path(folder_id: str) -> Path | None:
    """The folder's real location, which follows OneDrive backup and moved folders (Documents is
    often under OneDrive\\Documents, not C:\\Users\\name\\Documents)."""
    guid = uuid.UUID(folder_id)
    g = _GUID(guid.time_low, guid.time_mid, guid.time_hi_version, (ctypes.c_ubyte * 8)(*guid.bytes[8:]))
    out = ctypes.c_wchar_p()
    shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(_GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
    if shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) != 0:
        return None
    try:
        return Path(out.value)
    finally:
        ole32.CoTaskMemFree(out)


# Linux keeps the same folders in ~/.config/user-dirs.dirs (names can be translated, e.g. ~/Dokumente).
_XDG_NAMES = {"documents": "DOCUMENTS", "desktop": "DESKTOP", "downloads": "DOWNLOAD", "pictures": "PICTURES"}


def _xdg_path(key: str) -> Path | None:
    name = _XDG_NAMES.get(key)
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "user-dirs.dirs"
    if not name or not cfg.is_file():
        return None
    try:
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(f"XDG_{name}_DIR="):
                value = line.split("=", 1)[1].strip().strip('"')
                value = value.replace("$HOME", str(Path.home()))
                path = Path(value)
                # XDG points a missing folder at $HOME itself; that is not the folder asked for.
                return path if path != Path.home() else None
    except OSError:
        log.exception("could not read %s", cfg)
    return None


def locate(key: str) -> Path | None:
    _label, folder_id, fallback, _default = COMMON[key]
    path = None
    if sys.platform == "win32":
        try:
            path = _windows_path(folder_id)
        except Exception:
            log.exception("known-folder lookup failed for %s", key)
    elif sys.platform.startswith("linux"):
        path = _xdg_path(key)
    if path is None or not path.is_dir():
        path = Path.home() / fallback
    return path.resolve() if path.is_dir() else None


def enabled() -> bool:
    # Adding a folder means indexing (storing) its text, so it never happens in names-only mode.
    return settings.store_file_text and bool(prefs.get(PREF_ENABLED, True))


def set_enabled(value: bool) -> None:
    prefs.set(PREF_ENABLED, bool(value))


def _offered() -> list[str]:
    done = prefs.get(PREF_DONE, [])
    return [k for k in done if k in COMMON] if isinstance(done, list) else []


def _mark_offered(keys: list[str]) -> None:
    prefs.set(PREF_DONE, sorted(set(_offered()) | set(keys)))


def listing(indexed_roots: list[str]) -> list[dict]:
    """Each common folder: where it is, whether it exists, and whether it is already indexed."""
    from app.core.personalize import norm_path

    have = {norm_path(r) for r in indexed_roots}
    rows = []
    for key, (label, _fid, _fb, default) in COMMON.items():
        path = locate(key)
        rows.append(
            {
                "key": key,
                "label": label,
                "path": str(path) if path else None,
                "exists": path is not None,
                "indexed": bool(path) and norm_path(path) in have,
                "default": default,
            }
        )
    return rows


def add(keys: list[str], conn) -> list[Path]:
    """Registers the folders as indexed roots with the default exclusions. Returns the ones that
    are new, for the caller to scan. Unknown keys and folders that don't exist are skipped."""
    from app.core import personalize
    from app.db import sqlite_fts

    added: list[Path] = []
    existing = {personalize.norm_path(p) for p in sqlite_fts.root_paths(conn)}
    for key in keys:
        if key not in COMMON:
            continue
        path = locate(key)
        if path is None or personalize.norm_path(path) in existing:
            continue
        sqlite_fts.add_root(conn, path)
        personalize.set_folder_profile(path, {"label": COMMON[key][0], "exclude_globs": DEFAULT_EXCLUDES})
        added.append(path)
    _mark_offered(keys)
    return added


def startup_keys() -> list[str]:
    """Folders to add at launch: the on-by-default ones not offered before, if auto access is on."""
    if not enabled():
        return []
    offered = set(_offered())
    return [k for k, (_l, _f, _b, default) in COMMON.items() if default and k not in offered]
