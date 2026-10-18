"""The Windows Recycle Bin, read straight from disk so the Organize page can show what is in it and
empty it for good.

Windows keeps each deleted item as two entries in `<drive>:\\$Recycle.Bin\\<your SID>\\`:
  $I<id><ext>  a small record: format version, original size, deletion time, original full path
  $R<id><ext>  the file or folder itself, renamed
Only your own SID folder can be listed without administrator rights, so this never sees other users'
items. Items are identified by the path of their $I record, and every action re-reads the bin first and
acts only on items found there: no path from the browser is ever deleted directly.
"""
from __future__ import annotations

import os
import shutil
import stat
import struct
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)  # Windows FILETIME counts 100 ns from here


def bin_dirs() -> list[Path]:
    """Your Recycle Bin folder on each drive (empty off Windows)."""
    if sys.platform != "win32":
        return []
    import ctypes

    mask = ctypes.windll.kernel32.GetLogicalDrives()  # type: ignore[attr-defined]
    found = []
    for i in range(26):
        if not mask & (1 << i):
            continue
        root = Path(f"{chr(65 + i)}:\\$Recycle.Bin")
        try:
            for sid in root.iterdir():
                try:
                    next(sid.iterdir(), None)  # raises PermissionError on other users' folders
                    found.append(sid)
                except OSError:
                    continue
        except OSError:
            continue
    return found


def parse_record(data: bytes) -> tuple[int, datetime | None, str] | None:
    """(original size, deleted at, original path) from a $I record, or None when it isn't readable."""
    if len(data) < 28:
        return None
    version, size, stamp = struct.unpack_from("<qqq", data, 0)
    if version == 2:
        (chars,) = struct.unpack_from("<i", data, 24)
        raw = data[28:28 + max(chars, 0) * 2]
    elif version == 1:
        raw = data[24:24 + 520]
    else:
        return None
    path = raw.decode("utf-16-le", errors="replace").split("\x00", 1)[0]
    if not path:
        return None
    try:
        when = _EPOCH + timedelta(microseconds=stamp // 10) if stamp > 0 else None
    except OverflowError:
        when = None
    return size, when, path


def list_items(dirs: list[Path] | None = None) -> list[dict]:
    """Everything in the bin, newest deletion first: {id, name, original, folder, size, is_dir, deleted_at}."""
    items = []
    for d in bin_dirs() if dirs is None else dirs:
        try:
            records = [p for p in d.iterdir() if p.name.startswith("$I")]
        except OSError:
            continue
        for rec in records:
            data_path = rec.with_name("$R" + rec.name[2:])
            try:
                parsed = parse_record(rec.read_bytes())
                if parsed is None or not os.path.lexists(data_path):
                    continue
                size, when, original = parsed
                is_dir = data_path.is_dir() and not data_path.is_symlink()
                if when is None:
                    when = datetime.fromtimestamp(rec.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            name = original.replace("/", "\\").rsplit("\\", 1)[-1] or original
            items.append({
                "id": str(rec), "name": name, "original": original,
                "folder": original[: len(original) - len(name)].rstrip("\\") or original,
                "size": size, "is_dir": is_dir, "deleted_at": when.isoformat(timespec="seconds"),
            })
    return sorted(items, key=lambda i: i["deleted_at"], reverse=True)


def _writable(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)  # read-only files can't be removed until that is cleared
    func(path)


def _is_link(p: Path) -> bool:
    try:
        st = p.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & 0x400 and not stat.S_ISREG(st.st_mode))


def erase(path: Path) -> None:
    """Deletes a file or folder for good (no Recycle Bin). A link is removed itself; its target is left alone."""
    if _is_link(path):
        try:
            path.unlink()
        except (PermissionError, IsADirectoryError):
            os.rmdir(path)
    elif path.is_dir():
        shutil.rmtree(path, onerror=_writable)
    else:
        try:
            path.unlink()
        except PermissionError:
            os.chmod(path, stat.S_IWRITE)
            path.unlink()


def _pick(ids: list[str], dirs: list[Path] | None) -> tuple[list[dict], list[str]]:
    by_id = {i["id"]: i for i in list_items(dirs)}
    chosen = [by_id[i] for i in dict.fromkeys(ids) if i in by_id]
    return chosen, [i for i in ids if i not in by_id]


def purge(ids: list[str], dirs: list[Path] | None = None) -> list[dict]:
    """Deletes the chosen items for good. Returns one {item, ok, error?} per id; unknown ids are reported."""
    chosen, missing = _pick(ids, dirs)
    out = []
    for item in chosen:
        rec = Path(item["id"])
        try:
            erase(rec.with_name("$R" + rec.name[2:]))
            erase(rec)
            out.append({"item": item, "ok": True})
        except OSError as exc:
            out.append({"item": item, "ok": False, "error": str(exc)})
    out += [{"item": {"id": m, "name": m}, "ok": False, "error": "no longer in the Recycle Bin"} for m in missing]
    return out


def _free(path: Path) -> Path:
    if not path.exists():
        return path
    for n in range(2, 1000):
        cand = path.with_name(f"{path.stem} (restored{'' if n == 2 else f' {n}'}){path.suffix}")
        if not cand.exists():
            return cand
    raise FileExistsError(f"too many files named like {path.name}")


def restore(ids: list[str], dirs: list[Path] | None = None) -> list[dict]:
    """Puts the chosen items back where they were deleted from (recreating the folder if needed). A name
    that is taken again gets "(restored)" added; nothing is overwritten."""
    chosen, missing = _pick(ids, dirs)
    out = []
    for item in chosen:
        rec = Path(item["id"])
        try:
            dest = Path(item["original"])
            if not dest.is_absolute() or ":" in dest.name:
                raise ValueError("its original location can't be used")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest = _free(dest)
            shutil.move(str(rec.with_name("$R" + rec.name[2:])), str(dest))
            erase(rec)
            out.append({"item": item, "ok": True, "to": str(dest)})
        except (OSError, ValueError) as exc:
            out.append({"item": item, "ok": False, "error": str(exc)})
    out += [{"item": {"id": m, "name": m}, "ok": False, "error": "no longer in the Recycle Bin"} for m in missing]
    return out
