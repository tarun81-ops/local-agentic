"""Reversible file changes. Every move, copy and new folder is logged *before* it happens, grouped by
batch (one approved Organize action = one batch), so a whole batch can be undone: moves go back, copies
go to the Recycle Bin, new folders are removed if they are still empty. Recycle Bin deletes are logged
for the history but are restored from the Recycle Bin itself."""
import errno
import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

LOG_NAME = "undo_log.jsonl"
_LOCK = threading.Lock()


def _log_path(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / LOG_NAME


def _read(log_dir: Path) -> list[dict]:
    path = _log_path(log_dir)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write(log_dir: Path, entries: list[dict]) -> None:
    path = _log_path(log_dir)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    os.replace(tmp, path)


def _append(log_dir: Path, entry: dict) -> None:
    with open(_log_path(log_dir), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _drop(log_dir: Path, entry_id: str) -> None:
    _write(log_dir, [e for e in _read(log_dir) if e["id"] != entry_id])


def new_batch() -> str:
    return uuid.uuid4().hex


def _raw_move(src: Path, dst: Path) -> None:
    """Never overwrites. os.rename refuses an existing destination on Windows; across drives
    it fails with EXDEV and we copy, re-checking the destination first. Works for folders too."""
    if dst.exists() and os.path.normcase(str(dst)) != os.path.normcase(str(src)):  # a case-only rename is fine
        raise FileExistsError(f"destination already exists: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dst)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        if dst.exists():
            raise FileExistsError(f"destination already exists: {dst}") from exc
        if src.is_dir():
            try:
                shutil.copytree(src, dst, symlinks=True)
            except Exception:
                shutil.rmtree(dst, ignore_errors=True)  # our half-made copy; the original is untouched
                raise
            shutil.rmtree(src)  # only after the whole copy succeeded
        else:
            shutil.copy2(src, dst)
            os.unlink(src)


def _entry(batch: str, op: str, src: Path | None, dst: Path | None) -> dict:
    return {"id": uuid.uuid4().hex, "batch": batch, "op": op, "from": str(src) if src else None,
            "to": str(dst) if dst else None, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def move(from_path: Path, to_path: Path, log_dir: Path, batch: str) -> dict:
    with _LOCK:
        if not from_path.exists():
            raise FileNotFoundError(f"source is missing: {from_path}")
        if to_path.exists() and os.path.normcase(str(to_path)) != os.path.normcase(str(from_path)):
            raise FileExistsError(f"destination already exists: {to_path}")
        entry = _entry(batch, "move", from_path, to_path)
        _append(log_dir, entry)  # logged first: a crash mid-move still leaves a record
        try:
            _raw_move(from_path, to_path)
        except Exception:
            _drop(log_dir, entry["id"])
            raise
        return entry


def copy(from_path: Path, to_path: Path, log_dir: Path, batch: str) -> dict:
    """Copies a file or folder; never overwrites. Undo sends the copy to the Recycle Bin."""
    with _LOCK:
        if not from_path.exists():
            raise FileNotFoundError(f"source is missing: {from_path}")
        if to_path.exists():
            raise FileExistsError(f"destination already exists: {to_path}")
        entry = _entry(batch, "copy", from_path, to_path)
        _append(log_dir, entry)
        try:
            to_path.parent.mkdir(parents=True, exist_ok=True)
            if from_path.is_dir():
                shutil.copytree(from_path, to_path, symlinks=True)
            else:
                shutil.copy2(from_path, to_path)
        except Exception:
            _drop(log_dir, entry["id"])
            # to_path didn't exist before (checked above), so whatever is there now is our half-made copy.
            if to_path.is_dir():
                shutil.rmtree(to_path, ignore_errors=True)
            elif to_path.exists():
                to_path.unlink(missing_ok=True)
            raise
        return entry


def make_dir(path: Path, log_dir: Path, batch: str) -> dict:
    with _LOCK:
        if path.exists():
            raise FileExistsError(f"already exists: {path}")
        entry = _entry(batch, "mkdir", None, path)
        _append(log_dir, entry)
        try:
            path.mkdir(parents=False)
        except Exception:
            _drop(log_dir, entry["id"])
            raise
        return entry


def record_trash(path: Path, log_dir: Path, batch: str) -> None:
    """Recycle Bin deletes can't be restored from here, but they belong in the history."""
    with _LOCK:
        _append(
            log_dir,
            {
                "id": uuid.uuid4().hex,
                "batch": batch,
                "op": "trash",
                "from": str(path),
                "to": None,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
        )


UNDOABLE = ("move", "copy", "mkdir")


def undo_batch(log_dir: Path, batch: str) -> list[dict]:
    """Undoes the batch newest first: moves go back, copies go to the Recycle Bin, new folders are
    removed if still empty. Anything that has changed since (moved again, spot taken, folder no longer
    empty) is reported and left as it is."""
    from app.core.file_ops.safe_delete import trash

    results = []
    with _LOCK:
        entries = _read(log_dir)
        for e in reversed([e for e in entries if e["batch"] == batch and e["op"] in UNDOABLE]):
            src, dst = Path(e["to"]), Path(e["from"]) if e["from"] else None
            try:
                if e["op"] == "move":
                    if not src.exists():
                        raise FileNotFoundError(f"no longer at {src}")
                    _raw_move(src, dst)
                elif e["op"] == "copy":
                    if src.exists():
                        trash(src)
                else:  # mkdir
                    if src.exists():
                        src.rmdir()  # fails (and is reported) if something was put in it since
                entries = [x for x in entries if x["id"] != e["id"]]
                results.append({"op": e["op"], "from": str(src), "to": str(dst) if dst else None, "ok": True})
            except Exception as exc:
                results.append({"op": e["op"], "from": str(src), "to": str(dst) if dst else None, "ok": False, "error": str(exc)})
        # Trash records of a fully undone batch stay: those files are still in the Recycle Bin.
        _write(log_dir, entries)
    return results


def undo_last(log_dir: Path) -> list[dict] | None:
    undoable = [b for b in history(log_dir) if b["undoable"]]
    return undo_batch(log_dir, undoable[0]["batch"]) if undoable else None


def history(log_dir: Path, limit: int = 20) -> list[dict]:
    """Batches, newest first: {batch, at, moves, copies, folders, trashed, undoable, entries}."""
    batches: dict[str, dict] = {}
    count = {"move": "moves", "copy": "copies", "mkdir": "folders", "trash": "trashed"}
    for e in _read(log_dir):
        b = batches.setdefault(e["batch"], {"batch": e["batch"], "at": e["at"], "moves": 0, "copies": 0, "folders": 0,
                                            "trashed": 0, "undoable": 0, "entries": []})
        b[count.get(e["op"], "trashed")] += 1
        b["undoable"] += e["op"] in UNDOABLE
        b["entries"].append(e)
        b["at"] = max(b["at"], e["at"])
    return sorted(batches.values(), key=lambda b: b["at"], reverse=True)[:limit]
