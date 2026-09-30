"""Reversible file moves. Every move is logged *before* it happens, grouped by batch (one
approved Organize plan = one batch), so a whole reorganization can be undone."""
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
    it fails with EXDEV and we copy, re-checking the destination first."""
    if dst.exists():
        raise FileExistsError(f"destination already exists: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dst)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        if dst.exists():
            raise FileExistsError(f"destination already exists: {dst}") from exc
        shutil.copy2(src, dst)
        os.unlink(src)


def move(from_path: Path, to_path: Path, log_dir: Path, batch: str) -> dict:
    with _LOCK:
        if not from_path.is_file():
            raise FileNotFoundError(f"source is missing: {from_path}")
        if to_path.exists():
            raise FileExistsError(f"destination already exists: {to_path}")
        entry = {
            "id": uuid.uuid4().hex,
            "batch": batch,
            "op": "move",
            "from": str(from_path),
            "to": str(to_path),
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _append(log_dir, entry)  # logged first: a crash mid-move still leaves a record
        try:
            _raw_move(from_path, to_path)
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


def undo_batch(log_dir: Path, batch: str) -> list[dict]:
    """Moves every file in the batch back, newest first. A move whose file has since changed
    place (or whose original spot is taken) is reported and left as it is."""
    results = []
    with _LOCK:
        entries = _read(log_dir)
        for e in reversed([e for e in entries if e["batch"] == batch and e["op"] == "move"]):
            src, dst = Path(e["to"]), Path(e["from"])
            try:
                if not src.is_file():
                    raise FileNotFoundError(f"no longer at {src}")
                _raw_move(src, dst)
                entries = [x for x in entries if x["id"] != e["id"]]
                results.append({"from": str(src), "to": str(dst), "ok": True})
            except Exception as exc:
                results.append({"from": str(src), "to": str(dst), "ok": False, "error": str(exc)})
        # Trash records of a fully undone batch stay: those files are still in the Recycle Bin.
        _write(log_dir, entries)
    return results


def undo_last(log_dir: Path) -> list[dict] | None:
    undoable = [b for b in history(log_dir) if b["moves"]]
    return undo_batch(log_dir, undoable[0]["batch"]) if undoable else None


def history(log_dir: Path, limit: int = 20) -> list[dict]:
    """Batches, newest first: {batch, at, moves, trashed, entries}."""
    batches: dict[str, dict] = {}
    for e in _read(log_dir):
        b = batches.setdefault(e["batch"], {"batch": e["batch"], "at": e["at"], "moves": 0, "trashed": 0, "entries": []})
        b["moves" if e["op"] == "move" else "trashed"] += 1
        b["entries"].append(e)
        b["at"] = max(b["at"], e["at"])
    return sorted(batches.values(), key=lambda b: b["at"], reverse=True)[:limit]
