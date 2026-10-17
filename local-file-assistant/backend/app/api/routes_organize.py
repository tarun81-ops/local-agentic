import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.core import indexer, known_folders, pc_access, tidy
from app.core.file_ops import file_mover, manager
from app.core.file_ops.paths import PathNotAllowed, same_file_key
from app.core.file_ops.safe_delete import trash
from app.core.parsers import PARSERS
from app.db import sqlite_fts
from app.models.organize import TIDY_CHECKS, ApplyRequest, OrganizePlan, TidyRequest, UndoRequest

router = APIRouter(prefix="/organize", tags=["organize"])


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


@router.post("/tidy")
def tidy_plan(req: TidyRequest) -> OrganizePlan:
    """A cleanup plan from names, sizes, dates and hashes only (no model). Nothing on disk changes here."""
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
    try:
        return tidy.propose(folder, large_bytes=max(1, req.large_mb) * tidy.MB, old_days=max(1, req.old_days), checks=checks)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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


@router.post("/apply")
def apply(req: ApplyRequest):
    """Runs actions the user approved (a Tidy-up plan, or Move & delete). Every action is checked first;
    if any is invalid, nothing is changed. Moves, copies and new folders are logged and can be undone as
    one batch; deletes go to the Recycle Bin."""
    if not req.actions:
        raise HTTPException(status_code=400, detail="No actions selected.")
    resolved, problems = manager.check_batch(req.actions, _allowed_roots())
    if problems:
        raise HTTPException(status_code=400, detail={"message": "Nothing was changed.", "problems": problems})

    batch = file_mover.new_batch()
    results = []
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
            else:
                was_file = src.is_file()
                trash(src)
                file_mover.record_trash(src, settings.data_dir, batch)
                if was_file:
                    indexer.remove_file(src, settings.db_path, settings.vector_db_dir)
            results.append({"op": action.op, "path": str(src or dst), "to": str(dst) if dst and src else None, "ok": True})
        except Exception as exc:
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
