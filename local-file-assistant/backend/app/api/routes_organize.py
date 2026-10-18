import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.core import indexer, known_folders, pc_access, tidy
from app.core.file_ops import file_mover, manager, recycle_bin
from app.core.file_ops.paths import PathNotAllowed, same_file_key
from app.core.file_ops.safe_delete import count_entries, erase, trash
from app.core.parsers import PARSERS
from app.db import sqlite_fts
from app.models.organize import TIDY_CHECKS, ApplyRequest, OrganizePlan, TidyRequest, UndoRequest

router = APIRouter(prefix="/organize", tags=["organize"])
log = logging.getLogger(__name__)
BIN_SHOWN = 1000  # rows the Recycle Bin tab gets; counts and sizes still cover everything


def _roots() -> list[str]:
    conn = sqlite_fts.connect(settings.db_path)
    try:
        return sqlite_fts.root_paths(conn)
    finally:
        conn.close()


# Tidy-up works on these everyday folders even when they aren't indexed (Downloads is the usual mess).
TIDY_FOLDERS = ("downloads", "desktop")


def _tidy_folders() -> list[dict]:
    rows = []
    for key in TIDY_FOLDERS:
        path = known_folders.locate(key)
        if path is not None:
            rows.append({"key": key, "label": known_folders.COMMON[key][0], "path": str(path)})
    return rows


def _allowed_roots() -> list[str]:
    """Where approved actions may touch files: the indexed folders plus the tidy folders."""
    return _roots() + [r["path"] for r in _tidy_folders()]


@router.get("/tidy/folders")
def tidy_folders():
    """Folders Tidy-up can work on: Downloads and Desktop first, then the indexed folders."""
    rows = _tidy_folders()
    have = {same_file_key(Path(r["path"])) for r in rows}
    for root in _roots():
        if same_file_key(Path(root)) not in have:
            rows.append({"key": None, "label": Path(root).name or root, "path": root})
    return {"folders": rows}


def _tidy_target(req: TidyRequest) -> tuple[Path, list[str]]:
    """The folder to scan and the checks to run, or an HTTP error saying why not."""
    if req.folder is None:
        folder = known_folders.locate("downloads")
        if folder is None:
            raise HTTPException(status_code=404, detail="Couldn't find your Downloads folder. Pick a folder instead.")
    else:
        try:
            folder = manager.allowed(req.folder, _allowed_roots())
        except PathNotAllowed as exc:
            raise HTTPException(status_code=400, detail=f"Tidy-up can't work there: {exc}") from exc
        if same_file_key(folder) in {same_file_key(Path(d).resolve()) for d in pc_access.config()["drives"]}:
            raise HTTPException(status_code=400, detail="Pick a folder, not a whole drive.")
    checks = [c for c in req.checks if c in TIDY_CHECKS]
    if not checks:
        raise HTTPException(status_code=400, detail="Pick at least one thing to look for.")
    return folder, checks


@router.post("/tidy")
def tidy_plan(req: TidyRequest) -> OrganizePlan:
    """A cleanup plan from names, sizes, dates and hashes only (no model). Nothing on disk changes here.
    The Organize page uses /tidy/start instead, which reports progress; this waits for the whole scan."""
    folder, checks = _tidy_target(req)
    try:
        return tidy.propose(folder, large_bytes=max(1, req.large_mb) * tidy.MB, old_days=max(1, req.old_days), checks=checks)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# Scans run in a background thread so a big folder shows progress (and can be stopped) instead of a frozen button.
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
MAX_JOBS = 8


def _run_job(job_id: str, folder: Path, req: TidyRequest, checks: list[str]) -> None:
    job = _JOBS[job_id]

    def tick(phase: str, done: int, total: int | None) -> None:
        if job["cancel"]:
            raise tidy.Cancelled()
        job.update(phase=phase, done=done, total=total)

    try:
        plan = tidy.propose(folder, large_bytes=max(1, req.large_mb) * tidy.MB, old_days=max(1, req.old_days), checks=checks, tick=tick)
        job.update(state="done", plan=plan.model_dump())
    except tidy.Cancelled:
        job.update(state="cancelled")
    except FileNotFoundError as exc:
        job.update(state="error", error=str(exc))
    except Exception as exc:  # a scan must end in a state the page can show, never hang
        log.exception("tidy scan failed for %s", folder)
        job.update(state="error", error=f"The scan failed: {exc}")


@router.post("/tidy/start")
def tidy_start(req: TidyRequest):
    """Starts a scan in the background; poll GET /tidy/jobs/{id}. A newer scan stops any older one."""
    folder, checks = _tidy_target(req)
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {folder}")
    job_id = uuid.uuid4().hex
    with _JOBS_LOCK:
        for old in _JOBS.values():
            if old["state"] == "running":
                old["cancel"] = True
        _JOBS[job_id] = {"state": "running", "cancel": False, "phase": "Starting", "done": 0, "total": None, "plan": None,
                         "error": None, "at": time.time()}
        for stale in sorted((k for k, v in _JOBS.items() if v["state"] != "running"), key=lambda k: _JOBS[k]["at"])[: max(0, len(_JOBS) - MAX_JOBS)]:
            del _JOBS[stale]
    threading.Thread(target=_run_job, args=(job_id, folder, req, checks), daemon=True, name=f"tidy-{job_id[:6]}").start()
    return {"job": job_id}


def _job(job_id: str) -> dict:
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="That scan is no longer available. Press Find clutter again.")
    return job


@router.get("/tidy/jobs/{job_id}")
def tidy_job(job_id: str):
    job = _job(job_id)
    return {"state": job["state"], "phase": job["phase"], "done": job["done"], "total": job["total"],
            "plan": job["plan"], "error": job["error"]}


@router.post("/tidy/jobs/{job_id}/cancel")
def tidy_cancel(job_id: str):
    job = _job(job_id)
    if job["state"] == "running":
        job["cancel"] = True
    return {"state": job["state"]}


def _validate(actions, roots: list[str]):
    """The checks every change goes through (also used by chat's move_file tool)."""
    return manager.check_batch(actions, roots)


@router.get("/places")
def places():
    """Quick places for Move & delete: Desktop, Downloads, Documents... and the PC access drives."""
    return {"places": manager.places(), "pc_access": pc_access.enabled()}


@router.get("/browse")
def browse(path: str, hidden: bool = False):
    """One folder's files and sub-folders (nothing is read, only listed)."""
    try:
        return manager.browse(path, _allowed_roots(), show_hidden=hidden)
    except PathNotAllowed as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


class _PathBody(BaseModel):
    path: str


def _shown(path: str) -> Path:
    # "notes.txt:x.exe" is an NTFS stream inside a file, and its suffix hides what it is; real names have no ":".
    if ":" in Path(path).name:
        raise HTTPException(status_code=400, detail=f"Not a file or folder name: {Path(path).name}")
    try:
        p = manager.allowed(path, _allowed_roots())
    except PathNotAllowed as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"No longer there: {path}")
    return p


@router.post("/open")
def open_item(body: _PathBody):
    """Opens a file in its app, or a folder in File Explorer. Programs, scripts and shortcuts are never
    opened from here (that would run them); the page shows them in File Explorer instead."""
    p = _shown(body.path)
    if p.is_file() and manager.is_runnable(p):
        raise HTTPException(status_code=400, detail=f"{p.name} is a program or script, so it isn't opened from here. Use Show in Explorer.")
    if sys.platform == "win32":
        os.startfile(p)  # noqa: S606 - checked above: a path this page may show, not a typed command
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(p)])  # noqa: S603
    return {"opened": str(p)}


@router.post("/reveal")
def reveal_item(body: _PathBody):
    """Opens the item's folder in File Explorer with the item selected."""
    p = _shown(body.path)
    if sys.platform == "win32":
        subprocess.Popen(f'explorer.exe /select,"{p}"')  # noqa: S603 - path checked above
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(p.parent)])  # noqa: S603
    return {"revealed": str(p)}


def _screen(actions, skip_missing: bool) -> tuple[list, list[dict], list[str]]:
    """Before anything runs: sets aside (skip_missing) or refuses what has vanished since the plan was made, and
    refuses a duplicate delete whose kept copy is gone or changed, or is itself being deleted in the same batch.
    Returns (actions to check and run, skipped results, problems)."""
    keep_going, skipped, problems = [], [], []
    doomed = {str(Path(a.path).resolve(strict=False)).lower() for a in actions if a.op in ("delete", "delete_forever")}
    for a in actions:
        why = None
        if a.op != "mkdir" and not os.path.lexists(a.path):
            why = f"no longer exists: {a.path}"
        elif a.keep:
            keeper = Path(a.keep)
            try:
                changed = not keeper.is_file() or (a.size is not None and keeper.stat().st_size != a.size)
            except OSError:
                changed = True
            if changed:
                why = f"{Path(a.path).name}: the copy it duplicates ({keeper.name}) is gone or has changed, so it is left alone. Scan again"
            elif str(keeper.resolve(strict=False)).lower() in doomed:
                problems.append(f"{keeper.name} is the copy being kept, so it can't also be deleted in the same batch")
                continue
        if why is None:
            keep_going.append(a)
        elif skip_missing:
            skipped.append({"op": a.op, "path": a.path, "to": None, "ok": True, "skipped": True})
        else:
            problems.append(why)
    return keep_going, skipped, problems


@router.post("/apply")
def apply(req: ApplyRequest):
    """Runs actions the user approved (a Tidy-up plan, or Move & delete). Every action is checked first;
    if any is invalid, nothing is changed. Moves, copies and new folders are logged and can be undone as
    one batch; deletes go to the Recycle Bin, except "delete_forever" (explicit, can't be undone)."""
    if not req.actions:
        raise HTTPException(status_code=400, detail="No actions selected.")
    actions, results, problems = _screen(req.actions, req.skip_missing)
    resolved, more = manager.check_batch(actions, _allowed_roots())
    problems += more
    if problems:
        raise HTTPException(status_code=400, detail={"message": "Nothing was changed.", "problems": problems})

    batch = file_mover.new_batch()
    for action, src, dst in resolved:
        try:
            if action.op == "mkdir":
                file_mover.make_dir(dst, settings.data_dir, batch)
            elif action.op in ("move", "rename"):
                was_file = src.is_file()
                file_mover.move(src, dst, settings.data_dir, batch)
                if was_file:
                    indexer.remove_file(src, settings.db_path, settings.vector_db_dir)
                    if dst.suffix.lower() in PARSERS:  # installers, zips and videos have nothing to index
                        indexer.index_file(dst, settings.db_path, settings.vector_db_dir)
            elif action.op == "copy":
                file_mover.copy(src, dst, settings.data_dir, batch)
            elif action.op == "delete_forever":
                was_file = src.is_file()
                before = 0 if was_file else count_entries(src)
                try:
                    erase(src)
                except Exception:
                    # A folder an open file stopped halfway: part of it is gone for good, so the history says so.
                    if before and src.exists() and count_entries(src) < before:
                        file_mover.record_purge(src, settings.data_dir, batch, partial=True)
                    raise
                file_mover.record_purge(src, settings.data_dir, batch)
                if was_file:
                    indexer.remove_file(src, settings.db_path, settings.vector_db_dir)
            else:
                was_file = src.is_file()
                trash(src)
                file_mover.record_trash(src, settings.data_dir, batch)
                if was_file:
                    indexer.remove_file(src, settings.db_path, settings.vector_db_dir)
            results.append({"op": action.op, "path": str(src or dst), "to": str(dst) if dst and src else None, "ok": True})
        except Exception as exc:
            log.warning("organize %s failed for %s: %s", action.op, src or dst, exc)
            results.append({"op": action.op, "path": str(src or dst), "to": str(dst) if dst and src else None, "ok": False, "error": str(exc)})
    return {"batch": batch, "results": results}


@router.post("/undo")
def undo(req: UndoRequest):
    results = (
        file_mover.undo_batch(settings.data_dir, req.batch) if req.batch else file_mover.undo_last(settings.data_dir)
    )
    if results is None:
        raise HTTPException(status_code=404, detail="Nothing to undo.")
    for r in results:
        # Only a file moved back changes what the index knows; copies and new folders were never indexed.
        if r["ok"] and r.get("op", "move") == "move" and r["to"] and Path(r["to"]).is_file():
            indexer.remove_file(r["from"], settings.db_path, settings.vector_db_dir)
            if Path(r["to"]).suffix.lower() in PARSERS:
                indexer.index_file(Path(r["to"]), settings.db_path, settings.vector_db_dir)
    return {"results": results}


@router.get("/history")
def history():
    return {"batches": file_mover.history(settings.data_dir)}


# ---------------------------------------------------------------- Recycle Bin

class _BinIds(BaseModel):
    ids: list[str]


def _bin_summary(items: list[dict]) -> dict:
    return {"items": items[:BIN_SHOWN], "count": len(items), "bytes": sum(i["size"] for i in items),
            "truncated": len(items) > BIN_SHOWN, "supported": sys.platform == "win32"}


def _log_purged(results: list[dict]) -> None:
    for r in results:
        if not r["ok"]:
            log.warning("recycle bin: couldn't change %s: %s", r["item"].get("name"), r.get("error"))
    done = [r["item"] for r in results if r["ok"]]
    if done:
        batch = file_mover.new_batch()
        for item in done:
            file_mover.record_purge(Path(item["original"]), settings.data_dir, batch)


@router.get("/bin")
def bin_list():
    """What is in your Recycle Bin: name, where it was, size, when it was deleted."""
    return _bin_summary(recycle_bin.list_items())


@router.post("/bin/purge")
def bin_purge(body: _BinIds):
    """Deletes the chosen Recycle Bin items for good. This can't be undone."""
    if not body.ids:
        raise HTTPException(status_code=400, detail="Nothing selected.")
    results = recycle_bin.purge(body.ids)
    _log_purged(results)
    return {"results": results, **_bin_summary(recycle_bin.list_items())}


@router.post("/bin/restore")
def bin_restore(body: _BinIds):
    """Puts the chosen items back where they were deleted from."""
    if not body.ids:
        raise HTTPException(status_code=400, detail="Nothing selected.")
    results = recycle_bin.restore(body.ids)
    for r in results:
        if not r["ok"]:
            log.warning("recycle bin: couldn't restore %s: %s", r["item"].get("name"), r.get("error"))
    return {"results": results, **_bin_summary(recycle_bin.list_items())}


@router.post("/bin/empty")
def bin_empty():
    """Empties the Recycle Bin (everything listed by GET /bin) for good."""
    results = recycle_bin.purge([i["id"] for i in recycle_bin.list_items()])
    _log_purged(results)
    return {"results": results, **_bin_summary(recycle_bin.list_items())}
