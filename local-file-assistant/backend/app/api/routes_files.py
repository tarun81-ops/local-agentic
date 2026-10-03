import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.core import indexer, personalize
from app.core.assistant import learning
from app.core.watcher import watcher
from app.db import sqlite_fts

router = APIRouter(prefix="/files", tags=["files"])


class FolderRequest(BaseModel):
    folder: str


class PathRequest(BaseModel):
    path: str
    query: str = ""  # the search that led to opening it (optional; feeds search ranking)


def _conn():
    return sqlite_fts.connect(settings.db_path)


# One index run at a time: a single-user local app, not a job queue.
_status_lock = threading.Lock()
_COUNTS = {"seen": 0, "indexed": 0, "skipped": 0, "failed": 0, "removed": 0, "keyword_only": 0}
_status = {"state": "idle", "folder": None, "queue": [], "started": None, "error": None, **_COUNTS}


def _update(**kw):
    with _status_lock:
        _status.update(kw)


def _run_index(folders: list[Path]) -> None:
    """Scans folders one after another (a single folder when added from the Index page, all
    unscanned folders at startup)."""
    indexer.CANCEL.clear()
    try:
        for i, folder in enumerate(folders):
            if indexer.CANCEL.is_set():
                break
            _update(folder=str(folder), queue=[str(f) for f in folders[i + 1 :]], started=time.monotonic(), **_COUNTS)
            result = indexer.index_folder(folder, settings.db_path, settings.vector_db_dir, on_progress=lambda c: _update(**c))
            _update(**result)
        _update(state="cancelled" if indexer.CANCEL.is_set() else "done", queue=[])
    except Exception as exc:
        _update(state="error", error=f"{type(exc).__name__}: {exc}", queue=[])


def _start_index(folders: list[Path]) -> dict:
    with _status_lock:
        if _status["state"] == "running":
            raise HTTPException(status_code=409, detail=f"Already scanning {_status['folder']}. Try again when it finishes.")
        _status.update(state="running", folder=str(folders[0]), error=None, **_COUNTS)
    threading.Thread(target=_run_index, args=(folders,), name="index-run", daemon=True).start()
    return {"state": "started", "folder": str(folders[0])}


def rescan_unscanned() -> list[str]:
    """Called at startup: scans folders that were never fully scanned — new ones whose scan
    was interrupted, and all of them after an index schema upgrade."""
    conn = _conn()
    try:
        folders = [Path(p) for p in sqlite_fts.unscanned_roots(conn) if Path(p).is_dir()]
    finally:
        conn.close()
    if folders:
        try:
            _start_index(folders)
        except HTTPException:
            return []
    return [str(f) for f in folders]


def _existing_dir(folder: str) -> Path:
    path = Path(folder).expanduser()
    if not path.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a folder: {folder}")
    return path.resolve()


@router.get("/roots")
def roots():
    conn = _conn()
    try:
        rows = sqlite_fts.list_roots(conn)
    finally:
        conn.close()
    progress = _snapshot()
    running = progress["folder"] if progress["state"] == "running" else None
    queued = set(progress.get("queue") or [])
    for r in rows:
        r["profile"] = personalize.folder_profile(r["path"])
        if r["path"] == running:
            r["state"] = "scanning"
            r["progress"] = progress
        elif r["path"] in queued:
            r["state"] = "queued"
        elif not Path(r["path"]).is_dir():
            r["state"] = "missing"
        elif r["last_scan"] is None:
            r["state"] = "not_scanned"
        else:
            r["state"] = "up_to_date"
    return {"roots": rows}


@router.post("/roots")
def add_root(req: FolderRequest):
    folder = _existing_dir(req.folder)
    conn = _conn()
    try:
        sqlite_fts.add_root(conn, folder)
    finally:
        conn.close()
    watcher.refresh()
    return _start_index([folder])


class ProfileRequest(BaseModel):
    folder: str
    profile: dict


@router.put("/roots/profile")
def save_profile(req: ProfileRequest):
    """Saves a folder's rules, then rescans it so files the new rules exclude leave the index
    and newly allowed ones come in. If a scan is already running the rescan is left to the user."""
    folder = _existing_dir(req.folder)
    try:
        profile = personalize.set_folder_profile(folder, req.profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        _start_index([folder])
        rescan = "started"
    except HTTPException:
        rescan = "busy"
    return {"profile": profile, "rescan": rescan}


@router.post("/roots/remove")
def remove_root(req: FolderRequest):
    """Stops indexing a folder and forgets its entries. The files themselves are not touched."""
    removed = indexer.remove_root(req.folder, settings.db_path, settings.vector_db_dir)
    watcher.refresh()
    return {"removed": removed}


@router.post("/index")
def start_index(req: FolderRequest):
    return _start_index([_existing_dir(req.folder)])


@router.post("/index/cancel")
def cancel_index():
    """Stops the scan after the file it is on. Files done so far stay indexed; the next scan
    carries on from there (unchanged files are skipped quickly)."""
    with _status_lock:
        running = _status["state"] == "running"
    if running:
        indexer.CANCEL.set()
    return {"cancelling": running}


def _snapshot() -> dict:
    with _status_lock:
        current = dict(_status)
    started = current.pop("started", None)
    if current["state"] == "running" and started:
        minutes = max((time.monotonic() - started) / 60, 1e-6)
        current["files_per_min"] = round(current["seen"] / minutes, 1)
    return current


@router.get("/index/status")
def status():
    return {**_snapshot(), "errors": list(indexer.RECENT_ERRORS)}


RECENT_LIMIT, CHANGED_DAYS = 8, 7


@router.get("/recent")
def recent():
    """Files worth surfacing on the Chat home: the ones you opened last, then ones changed in the
    past week. Only files still in the index; opening goes through /files/open, so the
    open-history learning keeps working."""
    conn = _conn()
    try:
        rows, seen = [], set()
        for o in learning.recent_opens(RECENT_LIMIT * 3):
            if o["path"] not in seen and sqlite_fts.is_indexed(conn, o["path"]):
                seen.add(o["path"])
                rows.append({"path": o["path"], "name": Path(o["path"]).name, "reason": "opened", "at": o["at"]})
        for f in sqlite_fts.recent_files(conn, time.time() - CHANGED_DAYS * 86400, RECENT_LIMIT * 3):
            if f["path"] not in seen:
                seen.add(f["path"])
                rows.append({"path": f["path"], "name": Path(f["path"]).name, "reason": "changed", "at": f["mtime"]})
        return {"files": rows[:RECENT_LIMIT]}
    finally:
        conn.close()


@router.get("")
def list_files(root: str | None = None):
    conn = _conn()
    try:
        return {"files": sqlite_fts.list_files(conn, root)}
    finally:
        conn.close()


@router.post("/open")
def open_file(req: PathRequest):
    """Opens an indexed file in its default app. Only files in the index can be opened, so
    this can't be used to launch arbitrary programs."""
    conn = _conn()
    try:
        known = sqlite_fts.is_indexed(conn, req.path)
    finally:
        conn.close()
    path = Path(req.path)
    if not known or not path.is_file():
        raise HTTPException(status_code=404, detail="That file isn't in the index (it may have moved).")
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - path is an indexed document, not user-typed
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
    try:
        learning.record_open(str(path), req.query)
    except Exception:
        pass  # learning must never stop a file from opening
    return {"opened": str(path)}
