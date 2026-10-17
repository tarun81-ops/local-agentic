"""Move & delete: the rules for which files and folders the Organize page may list and change.

A path may be touched when it is inside an indexed folder, Downloads or Desktop, or (while PC access is
on) inside a drive PC access covers. System folders, credential-like files and links are never listed
or touched, by the same rules the PC file list uses. A few folders are never moved, renamed or deleted
themselves (drive roots, your home folder, Desktop/Documents/Downloads/...), only what is inside them.
Nothing here changes the disk: routes_organize validates a whole batch with check_batch() first and
only then carries it out through file_mover, so moves, copies and new folders can be undone and
deletes go to the Recycle Bin."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from app.core import known_folders, pc_access
from app.core.file_ops.paths import PathNotAllowed, name_problem, path_problem, same_file_key

MAX_LIST = 3000            # entries shown for one folder; the rest are counted, not listed
MAX_COPY_FILES = 20000      # a folder copy bigger than this is refused (it would block the app for minutes)
MAX_COPY_BYTES = 5 * 1024 ** 3  # same for size: copies, and moves to another drive (which are copies), up to 5 GB
# Opening these runs them: Open refuses them and the page offers Show in Explorer instead.
RUNNABLE_EXTS = {
    ".exe", ".bat", ".cmd", ".com", ".msi", ".msix", ".appx", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf",
    ".wsh", ".scr", ".lnk", ".url", ".reg", ".hta", ".jar", ".cpl", ".dll", ".msc", ".pif", ".inf", ".sh", ".desktop",
    ".appimage", ".deb", ".rpm", ".run", ".msp", ".mst", ".application", ".appref-ms", ".chm", ".gadget", ".vb",
    ".ws", ".scf", ".settingcontent-ms", ".library-ms", ".py", ".pyw",
}
_HIDDEN, _SYSTEM = 0x2, 0x4
LEAVE_ALONE = {"desktop.ini", "thumbs.db", ".ds_store"}

# Extra Windows known folders for the Places list (ids from KnownFolders.h).
_PLACES = [
    ("desktop", "Desktop", None), ("downloads", "Downloads", None), ("documents", "Documents", None),
    ("pictures", "Pictures", None),
    ("videos", "Videos", "18989B1D-99B5-455B-841C-AB7C74E4DDFC"),
    ("music", "Music", "4BD8D571-6D19-48D3-BE97-422220080E43"),
]


def _locate(key: str, folder_id: str | None) -> Path | None:
    if folder_id is None:
        return known_folders.locate(key)
    path = None
    if sys.platform == "win32":
        try:
            path = known_folders._windows_path(folder_id)
        except Exception:
            path = None
    if path is None or not path.is_dir():
        path = Path.home() / key.capitalize()
    return path.resolve() if path.is_dir() else None


def places() -> list[dict]:
    """Quick places for the file browser: everyday folders, then the drives PC access covers."""
    out, seen = [], set()
    for key, label, fid in _PLACES:
        p = _locate(key, fid)
        if p is not None and same_file_key(p) not in seen:
            seen.add(same_file_key(p))
            out.append({"label": label, "path": str(p), "kind": "folder"})
    cfg = pc_access.config()
    if cfg["enabled"]:
        for d in cfg["drives"]:
            if same_file_key(Path(d)) not in seen:
                out.append({"label": d.rstrip("\\/") or d, "path": d, "kind": "drive"})
    return out


def protected() -> list[Path]:
    """Main folders: never moved, renamed or deleted themselves, and neither is any folder that holds
    one (C:\\Users holds your home). What is inside them can be."""
    paths = [Path.home().resolve()]
    for key, _label, fid in _PLACES:
        p = _locate(key, fid)
        if p is not None:
            paths.append(p)
    onedrive = os.environ.get("OneDrive")
    if onedrive:
        paths.append(Path(onedrive).resolve())
    paths += [Path(d).resolve() for d in pc_access.config()["drives"]]
    return paths


def _holds_main_folder(src: Path, guarded: list[Path]) -> bool:
    key = same_file_key(src)
    return any(same_file_key(g) == key or same_file_key(g).startswith(key.rstrip("\\/") + os.sep) for g in guarded)


def is_runnable(path: str | Path) -> bool:
    """Would opening it run something? Also everything chat's open tool refuses, so the two lists can't drift."""
    from app.core.assistant.tools.builtin import BLOCKED_EXT  # late: the tools module registers tools on import

    suffix = Path(path).suffix.lower()
    return suffix in RUNNABLE_EXTS or suffix in BLOCKED_EXT


def _same_drive(a: Path, b: Path) -> bool:
    """Is a move from a to the folder b a rename (instant) rather than a copy? b may not exist yet."""
    while not b.exists() and b.parent != b:
        b = b.parent
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def _too_big_or_secret(src: Path) -> str | None:
    """Why copying src would be refused: too many files, too many bytes, or password/key files inside.
    Stops at the first reason, so a huge folder is not walked to the end."""
    if src.is_file():
        return "is over 5 GB" if src.stat().st_size > MAX_COPY_BYTES else None
    files = total = 0
    for folder, subdirs, names in os.walk(src):
        for n in subdirs + names:
            if pc_access.is_sensitive(os.path.join(folder, n)):
                return "holds password or key files"
        for n in names:
            files += 1
            try:
                total += os.path.getsize(os.path.join(folder, n))
            except OSError:
                pass
            if files > MAX_COPY_FILES:
                return f"has more than {MAX_COPY_FILES:,} files"
            if total > MAX_COPY_BYTES:
                return "is over 5 GB"
    return None


def allowed(path: str | Path, roots: list[str]) -> Path:
    """The resolved path, if the Organize page may list or change it; PathNotAllowed otherwise."""
    resolved = Path(path).resolve(strict=False)
    if pc_access.is_sensitive(resolved):
        raise PathNotAllowed(f"looks like a password or key file, so it is never touched: {path}")
    for root in roots:
        if resolved.is_relative_to(Path(root).resolve(strict=False)):
            return resolved
    cfg = pc_access.config()
    if cfg["enabled"] and pc_access._under(resolved, cfg["drives"]):
        if pc_access._skipped_part(resolved, cfg["extra_excludes"], cfg["drives"]):
            raise PathNotAllowed(f"inside a system or excluded folder: {path}")
        return resolved
    raise PathNotAllowed(f"outside the folders this app may change (turn on PC access to use any folder): {path}")


def browse(folder: str | Path, roots: list[str], show_hidden: bool = False) -> dict:
    """One folder's contents, folders first: [{name, path, is_dir, size, mtime, ext}]. Links, credential
    files, Windows' own desktop.ini/thumbs.db and (unless asked) hidden/system items are left out."""
    root = allowed(folder, roots)
    if not root.is_dir():
        raise FileNotFoundError(f"Folder not found: {folder}")
    entries, hidden_count = [], 0
    try:
        it = list(os.scandir(root))
    except OSError as exc:
        raise PermissionError(f"Can't open {root}: {exc.strerror or exc}") from exc
    for e in it:
        try:
            if pc_access._is_link(e):
                continue
            is_dir = e.is_dir(follow_symlinks=False)
            if not is_dir and not e.is_file(follow_symlinks=False):
                continue
            st = e.stat(follow_symlinks=False)
        except OSError:
            continue
        low = e.name.lower()
        if low in LEAVE_ALONE or pc_access.is_sensitive(e.path) or (is_dir and low in pc_access.SKIP_DIRS):
            continue
        attrs = getattr(st, "st_file_attributes", 0)
        if not show_hidden and (attrs & (_HIDDEN | _SYSTEM) or e.name.startswith(("$", "~$"))):
            hidden_count += 1
            continue
        entries.append({"name": e.name, "path": e.path, "is_dir": is_dir, "size": None if is_dir else st.st_size,
                        "mtime": st.st_mtime, "ext": "" if is_dir else Path(e.name).suffix.lower(),
                        "runnable": not is_dir and is_runnable(e.name)})
    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    parent = root.parent if root.parent != root else None
    try:
        parent_ok = parent is not None and allowed(parent, roots) is not None
    except PathNotAllowed:
        parent_ok = False
    crumbs = []
    for p in [*reversed(root.parents), root]:
        try:
            allowed(p, roots)
            crumbs.append({"name": p.name or str(p), "path": str(p)})
        except PathNotAllowed:
            continue
    return {"path": str(root), "parent": str(parent) if parent_ok else None, "crumbs": crumbs,
            "entries": entries[:MAX_LIST], "more": max(0, len(entries) - MAX_LIST), "hidden": hidden_count}


def free_target(folder: Path, name: str, taken: set[str]) -> Path:
    """folder/name, or "name (2).ext", "name (3).ext"... when the name is used on disk or earlier in the batch."""
    target = folder / name
    p = Path(name)
    stem, suffix = (p.stem, p.suffix) if p.suffix and not name.startswith(".") else (name, "")
    n = 2
    while target.exists() or same_file_key(target) in taken:
        target = folder / f"{stem} ({n}){suffix}"
        n += 1
    taken.add(same_file_key(target))
    return target


def check_batch(actions: list, roots: list[str]) -> tuple[list[tuple], list[str]]:
    """Checks every action of a batch before anything runs. Returns ([(action, src, dst)], problems).
    Ops: move / copy (to `to_folder`, or to the full path `to`), rename (`to` = new name in the same
    folder), mkdir (`path` = the new folder), delete (Recycle Bin). Files and folders both work."""
    resolved, problems = [], []
    sources: set[str] = set()
    targets: set[str] = set()
    new_dirs: set[str] = set()
    guarded = protected()  # main folders
    # New folders are checked first, so a move into a folder made in the same batch passes.
    for a in sorted(actions, key=lambda x: x.op != "mkdir"):
        try:
            if a.op == "mkdir":
                dst = allowed(a.path, roots)
                problem = path_problem(dst)
                if problem:
                    raise ValueError(problem)
                if dst.exists() or same_file_key(dst) in targets:
                    raise ValueError(f"already exists: {dst.name}")
                if not dst.parent.is_dir() and same_file_key(dst.parent) not in new_dirs:
                    raise ValueError(f"the folder to create it in doesn't exist: {dst.parent}")
                targets.add(same_file_key(dst))
                new_dirs.add(same_file_key(dst))
                resolved.append((a, None, dst))
                continue
            if Path(a.path).is_symlink():
                raise ValueError(f"is a link or shortcut, not a file: {a.path}")
            src = allowed(a.path, roots)
            if not (src.is_file() or src.is_dir()):
                raise ValueError(f"no longer exists: {a.path}")
            key = same_file_key(src)
            if key in sources:
                raise ValueError(f"listed twice: {a.path}")
            if a.op in ("move", "rename", "delete") and _holds_main_folder(src, guarded):
                raise ValueError(f"{src.name or src} is a main folder or holds one (like your user folder); "
                                 "it can't be moved, renamed or deleted (what's inside it can)")
            sources.add(key)
            dst = None
            if a.op in ("move", "copy"):
                if a.to_folder:
                    folder = allowed(a.to_folder, roots)
                    if not folder.is_dir() and same_file_key(folder) not in new_dirs:
                        raise ValueError(f"destination folder doesn't exist: {a.to_folder}")
                    if a.op == "move" and same_file_key(folder) == same_file_key(src.parent):
                        raise ValueError(f"{src.name} is already in that folder")
                    dst = free_target(folder, src.name, targets)  # keep both: never overwrite
                elif a.to:
                    dst = allowed(a.to, roots)
                    if dst.exists() or same_file_key(dst) in targets:
                        raise ValueError(f"destination already taken: {a.to}")
                    targets.add(same_file_key(dst))
                else:
                    raise ValueError(f"no destination for {a.path}")
                if src.is_dir() and (dst == src or dst.is_relative_to(src)):
                    raise ValueError(f"can't put the folder {src.name} inside itself")
                # A copy, or a move to another drive (which is a copy), is refused when it would take minutes or
                # spread password/key files. A move on the same drive is a rename: instant, nothing duplicated.
                if a.op == "copy" or not _same_drive(src, dst.parent):
                    why = _too_big_or_secret(src)
                    if why:
                        how = "copy it" if a.op == "copy" else "move it to the other drive"
                        raise ValueError(f"{src.name} {why}; {how} with File Explorer instead")
            elif a.op == "rename":
                name = (a.new_name if a.new_name is not None else a.to or "").strip()
                problem = name_problem(name)
                if problem:
                    raise ValueError(problem)
                dst = src.parent / name
                if dst == src:
                    raise ValueError(f"{src.name} already has that name")
                # Changing only the case ("report" -> "Report") is fine; any other existing name is not.
                if same_file_key(dst) != key and (dst.exists() or same_file_key(dst) in targets):
                    raise ValueError(f"a file or folder named {name} is already there")
                targets.add(same_file_key(dst))
            if dst is not None:
                problem = path_problem(dst)
                if problem:
                    raise ValueError(problem)
            resolved.append((a, src, dst))
        except (PathNotAllowed, ValueError) as exc:
            problems.append(str(exc))
    # New folders first, so moves into them work; deletes last.
    order = {"mkdir": 0, "rename": 1, "move": 1, "copy": 1, "delete": 2}
    resolved.sort(key=lambda r: order[r[0].op])
    return resolved, problems
