"""Tidy-up: a cleanup plan for a messy folder (Downloads by default), made from file names, sizes,
dates and content hashes only. No model is called and nothing on disk changes here: the plan goes
to the Organize page, the user ticks what to do, and /organize/apply carries it out (moves can be
undone, deletes go to the Recycle Bin).

What it proposes, in this order (a file lands in one group at most):
  1. Duplicates          - same bytes as another file in the folder (any depth). The copy with the
                           cleanest name is kept, then the one already inside a folder (someone put
                           it there on purpose), then the oldest. Only loose top-level copies are
                           ticked: a copy inside a sub-folder may belong to an unzipped program or a
                           folder you sorted, so it is listed unticked for you to check.
  2. Unfinished downloads - .crdownload/.part/... left over for more than a day. Ticked at the top
                           level, unticked inside sub-folders.
  3. Old large files     - big and not downloaded or changed for months. NOT ticked.
  4. Empty folders       - folders with nothing in them (only the outermost one of a nest). Ticked.
  5. Sort by type        - loose files at the top of the folder go into Documents, Images,
                           Installers, ... sub-folders. Files already in sub-folders stay put. Ticked.
Each kind can be switched off (`checks`). The plan also says what takes the space (`by_type`).

OneDrive "online-only" files are never read (that would download them) and never offered for the
Recycle Bin as old large files (they take no space on this PC).
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from collections import defaultdict
from pathlib import Path

from app.core import pc_access
from app.core.file_ops.paths import path_problem
from app.models.organize import OrganizeAction, OrganizePlan

MB = 1024 * 1024
LARGE_BYTES = 100 * MB
OLD_DAYS = 90
PARTIAL_GRACE_S = 24 * 3600          # a download in progress is never touched
MAX_FILES = 20000                    # a huge folder is cut off, not scanned forever
MAX_DEPTH = 6
HASH_SKIP_BYTES = 4 * 1024 * 1024 * 1024  # hashing a 4 GB+ file takes too long on a laptop disk

CATEGORIES: dict[str, set[str]] = {
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".md", ".epub", ".pages"},
    "Spreadsheets": {".xls", ".xlsx", ".xlsm", ".csv", ".ods", ".numbers"},
    "Presentations": {".ppt", ".pptx", ".odp", ".key"},
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".tif", ".tiff", ".svg", ".ico", ".avif"},
    "Videos": {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".webm", ".m4v", ".flv"},
    "Audio": {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz"},
    "Installers": {".exe", ".msi", ".msix", ".msixbundle", ".appx", ".appxbundle", ".apk", ".iso", ".img"},
    "Code": {".py", ".ipynb", ".js", ".ts", ".html", ".css", ".json", ".c", ".cpp", ".h", ".java", ".sql", ".sh", ".ps1", ".bat", ".xml", ".yaml", ".yml"},
    "Torrents": {".torrent"},
    "Fonts": {".ttf", ".otf", ".woff", ".woff2"},
}
EXT_TO_CATEGORY = {ext: cat for cat, exts in CATEGORIES.items() for ext in exts}
PARTIAL_EXTS = {".crdownload", ".part", ".partial", ".download", ".opdownload", ".tmp"}
# Never moved or binned: Windows and sync tools keep these here on purpose.
LEAVE_ALONE = {"desktop.ini", "thumbs.db", ".ds_store"}
_COPY_SUFFIX = re.compile(r"(\s*\(\d+\)|\s*-\s*copy(\s*\(\d+\))?|\s+copy(\s+\d+)?)$", re.I)
# Windows attributes of cloud placeholders (OneDrive Files On-Demand): reading them downloads them.
_OFFLINE, _RECALL_ON_OPEN, _RECALL_ON_DATA_ACCESS = 0x1000, 0x40000, 0x400000
_CLOUD_ONLY = _OFFLINE | _RECALL_ON_OPEN | _RECALL_ON_DATA_ACCESS


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def _is_link(entry: os.DirEntry) -> bool:
    # Same rule as the PC file list: real links and junctions are skipped; OneDrive's synced folders and
    # files (also reparse points) are real and are tidied.
    return pc_access._is_link(entry)


def scan(folder: Path) -> tuple[list[dict], bool]:
    """Every regular file under folder: [{path, depth, size, mtime, cloud}], plus whether it was cut off.
    Only the modified time is used for age: reading files to compare them updates the last-access time
    on Windows, so "last opened" would reset every time Tidy-up runs."""
    out: list[dict] = []
    stack = [(folder, 0)]
    while stack:
        cur, depth = stack.pop()
        try:
            entries = list(os.scandir(cur))
        except OSError:
            continue
        for e in entries:
            if _is_link(e):
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    if depth < MAX_DEPTH and not e.name.startswith((".", "$")) and e.name.lower() not in pc_access.SKIP_DIRS:
                        stack.append((Path(e.path), depth + 1))
                    continue
                if not e.is_file(follow_symlinks=False):
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if e.name.lower() in LEAVE_ALONE or e.name.startswith("~$") or pc_access.is_sensitive(e.path):
                continue
            attrs = getattr(st, "st_file_attributes", 0)
            out.append({"path": e.path, "depth": depth, "size": st.st_size, "mtime": st.st_mtime,
                        "cloud": bool(attrs & _CLOUD_ONLY)})
            if len(out) >= MAX_FILES:
                return out, True
    return out, False


def _sha256(path: str) -> str | None:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
    except OSError:
        return None
    return h.hexdigest()


def _quick_key(path: str) -> bytes | None:
    """First and last 64 KB: weeds out same-size files that differ before reading them whole."""
    try:
        with open(path, "rb") as f:
            head = f.read(65536)
            f.seek(max(0, os.path.getsize(path) - 65536))
            return head + f.read(65536)
    except OSError:
        return None


def duplicate_groups(files: list[dict]) -> list[list[dict]]:
    """Groups of identical files (size, then a quick check, then sha256). Empty files are ignored."""
    by_size: dict[int, list[dict]] = defaultdict(list)
    for f in files:
        if 0 < f["size"] < HASH_SKIP_BYTES and not f.get("cloud"):
            by_size[f["size"]].append(f)
    groups = []
    for same_size in by_size.values():
        if len(same_size) < 2:
            continue
        by_quick: dict[bytes, list[dict]] = defaultdict(list)
        for f in same_size:
            k = _quick_key(f["path"])
            if k is not None:
                by_quick[k].append(f)
        for cands in by_quick.values():
            if len(cands) < 2:
                continue
            by_hash: dict[str, list[dict]] = defaultdict(list)
            for f in cands:
                h = _sha256(f["path"])
                if h:
                    by_hash[h].append(f)
            groups += [g for g in by_hash.values() if len(g) > 1]
    return groups


def _keeper_rank(f: dict):
    """Lower is a better copy to keep: a name without "(1)"/"copy", then the one already inside a folder
    (deepest first: it was put there on purpose; the loose one is the clutter), then the oldest."""
    stem = Path(f["path"]).stem
    return (bool(_COPY_SUFFIX.search(stem)), -f["depth"], f["mtime"], len(Path(f["path"]).name))


def _free_target(folder: Path, name: str, taken: set[str]) -> Path:
    """folder/name, or "name (2).ext" ... when that is already used on disk or earlier in this plan."""
    target = folder / name
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 2
    while target.exists() or os.path.normcase(str(target)).lower() in taken:
        target = folder / f"{stem} ({n}){suffix}"
        n += 1
    taken.add(os.path.normcase(str(target)).lower())
    return target


ALL_CHECKS = ("duplicates", "partial", "old_large", "empty", "sort")


# Empty folders that apps create and expect to find; never offered for the Recycle Bin.
APP_FOLDERS = {
    "custom office templates", "my games", "zoom", "outlook files", "onenote notebooks", "my music", "my pictures",
    "my videos", "my shapes", "my data sources", "my received files", "sound recordings", "camera roll",
    "saved pictures", "screenshots", "captures", "visual studio 2019", "visual studio 2022", "github",
    "wpsdrive", "kingsoft", "fax", "scanned documents", "my kindle content", "adobe", "obs",
}


def empty_folders(root: Path) -> list[Path]:
    """Folders under root holding no files at any depth (Windows' desktop.ini/thumbs.db don't count),
    outermost only: if a/ holds only an empty b/, a/ is listed and b/ goes with it. Links, hidden
    and skipped folders are never entered or listed."""
    empty: dict[str, bool] = {}
    out: list[Path] = []

    def visit(folder: Path, depth: int) -> bool:
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return False
        is_empty = True
        children = []
        for e in entries:
            try:
                if _is_link(e):
                    is_empty = False
                    continue
                if e.is_dir(follow_symlinks=False):
                    if depth >= MAX_DEPTH or e.name.startswith((".", "$")) or e.name.lower() in pc_access.SKIP_DIRS:
                        is_empty = False
                        continue
                    sub = Path(e.path)
                    if e.name.lower() in APP_FOLDERS:
                        is_empty = False  # left alone, and so is the folder holding it
                        continue
                    if visit(sub, depth + 1):
                        children.append(sub)
                    else:
                        is_empty = False
                elif e.name.lower() not in LEAVE_ALONE:
                    is_empty = False
            except OSError:
                is_empty = False
        if not is_empty or depth == 0:
            out.extend(children)  # this folder stays, so its empty children are listed themselves
        return is_empty

    visit(root, 0)
    return sorted(out, key=lambda p: str(p).lower())


def by_type(files: list[dict]) -> list[dict]:
    """What takes the space: [{type, bytes, count}] per category, biggest first."""
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for f in files:
        cat = EXT_TO_CATEGORY.get(Path(f["path"]).suffix.lower(), "Other")
        totals[cat][0] += f["size"]
        totals[cat][1] += 1
    return sorted(({"type": k, "bytes": v[0], "count": v[1]} for k, v in totals.items()), key=lambda r: -r["bytes"])


def propose(folder: str | Path, now: float | None = None, large_bytes: int = LARGE_BYTES, old_days: int = OLD_DAYS,
            checks: list[str] | tuple[str, ...] = ALL_CHECKS) -> OrganizePlan:
    now = now or time.time()
    checks = set(checks)
    root = Path(folder).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Folder not found: {folder}")
    files, cut_off = scan(root)
    actions: list[OrganizeAction] = []
    handled: set[str] = set()
    kept: set[str] = set()  # the surviving copy of each duplicate group: never offered for deletion again
    freed = 0

    # 1. duplicates
    for group in duplicate_groups(files) if "duplicates" in checks else []:
        group.sort(key=_keeper_rank)
        keep = group[0]
        kept.add(keep["path"])
        for dup in group[1:]:
            loose = dup["depth"] == 0
            reason = f"Same file as {Path(keep['path']).relative_to(root)}"
            if not loose:
                reason += " (inside a folder: check it isn't part of an unzipped program)"
            actions.append(OrganizeAction(op="delete", path=dup["path"], group="Duplicates", size=dup["size"],
                                          selected=loose, reason=reason))
            handled.add(dup["path"])
            if loose:
                freed += dup["size"]

    # 2. unfinished downloads
    for f in files if "partial" in checks else []:
        if f["path"] in handled:
            continue
        if Path(f["path"]).suffix.lower() in PARTIAL_EXTS and now - f["mtime"] > PARTIAL_GRACE_S:
            loose = f["depth"] == 0
            actions.append(OrganizeAction(op="delete", path=f["path"], group="Unfinished downloads", size=f["size"],
                                          selected=loose,
                                          reason="Download that never finished" if loose else "Temporary file inside a folder: check before deleting"))
            handled.add(f["path"])
            if loose:
                freed += f["size"]

    # 3. old large files (not ticked). A kept duplicate is skipped: its other copies are already being
    # binned, so offering it too would let two ticks delete every copy.
    cutoff = now - old_days * 86400
    for f in files if "old_large" in checks else []:
        if f["path"] in handled or f["path"] in kept or f["cloud"]:
            continue
        if f["size"] >= large_bytes and f["mtime"] < cutoff:
            days = int((now - f["mtime"]) // 86400)
            actions.append(OrganizeAction(op="delete", path=f["path"], group="Old large files", size=f["size"], selected=False,
                                          reason=f"{_human(f['size'])}, downloaded or last changed {days} days ago"))
            handled.add(f["path"])

    # 4. empty folders
    empties = empty_folders(root) if "empty" in checks else []
    for d in empties:
        top = d.parent == root
        actions.append(OrganizeAction(op="delete", path=str(d), group="Empty folders", size=0, selected=top,
                                      reason="Empty folder" if top else "Empty folder inside a folder: check an app doesn't use it"))

    # 5. sort loose top-level files by type
    taken: set[str] = set()
    for f in sorted(files, key=lambda x: Path(x["path"]).name.lower()) if "sort" in checks else []:
        if f["path"] in handled or f["depth"] != 0:
            continue
        p = Path(f["path"])
        category = EXT_TO_CATEGORY.get(p.suffix.lower())
        if category is None or p.parent.name == category:
            continue
        dest = _free_target(root / category, p.name, taken)
        if path_problem(dest):
            continue
        reason = f"{p.suffix.lower()} file" + (" (renamed: the name was taken)" if dest.name != p.name else "")
        actions.append(OrganizeAction(op="move", path=str(p), to=str(dest), group=f"Sort by type/{category}", size=f["size"], reason=reason))
        handled.add(f["path"])

    moves = sum(a.op == "move" for a in actions)
    bins = sum(a.op == "delete" and a.selected and a.group != "Empty folders" for a in actions)
    empties = [d for d in empties if d.parent == root]  # the summary counts the ticked ones; the rest are "unticked"
    optional = sum(a.op == "delete" and not a.selected for a in actions)
    if actions:
        parts = [f"{moves} file{'s' if moves != 1 else ''} to sort into folders",
                 f"{bins} to send to the Recycle Bin (frees {_human(freed)})"]
        if empties:
            parts.append(f"{len(empties)} empty folder{'s' if len(empties) != 1 else ''} to remove")
        if optional:
            parts.append(f"{optional} more left unticked for you to decide on")
        summary = ", ".join(parts) + "."
    else:
        summary = "Nothing to tidy. This folder already looks neat."
    if cut_off:
        summary += f" Only the first {MAX_FILES} files were looked at."
    return OrganizePlan(summary=summary, root=str(root), actions=actions, kind="tidy",
                        freed_bytes=freed, scanned=len(files), by_type=by_type(files))
