from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.core import indexer
from app.core.file_ops import file_mover
from app.core.file_ops.paths import PathNotAllowed, inside_roots, path_problem, same_file_key
from app.core.file_ops.safe_delete import trash
from app.core.llm.organizer import propose_plan
from app.db import sqlite_fts
from app.models.organize import ApplyRequest, OrganizeAction, OrganizePlan, PlanRequest, UndoRequest

router = APIRouter(prefix="/organize", tags=["organize"])


def _roots() -> list[str]:
    conn = sqlite_fts.connect(settings.db_path)
    try:
        return sqlite_fts.root_paths(conn)
    finally:
        conn.close()


@router.post("/plan")
def plan(req: PlanRequest) -> OrganizePlan:
    """Proposes a plan only. Nothing on disk changes here."""
    roots = _roots()
    root = req.root or (roots[0] if len(roots) == 1 else None)
    if root is None or root not in roots:
        raise HTTPException(status_code=400, detail="Choose one of your indexed folders to organize.")
    conn = sqlite_fts.connect(settings.db_path)
    try:
        files = sqlite_fts.list_files(conn, root)
        hints = sqlite_fts.first_texts(conn, root)
        for f in files:
            f["hint"] = hints.get(f["path"], "")
        dups = sqlite_fts.duplicate_groups(conn, root)
    finally:
        conn.close()
    try:
        return propose_plan(root, files, dups)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"The local model couldn't make a plan: {exc}") from exc


def _validate(actions: list[OrganizeAction], roots: list[str]) -> tuple[list[tuple], list[str]]:
    """Checks every action before any runs. Returns (resolved actions, problems)."""
    resolved, problems = [], []
    sources: set[str] = set()
    targets: set[str] = set()
    for a in actions:
        try:
            if Path(a.path).is_symlink():
                # Moving a link would really move (or trash) whatever it points at.
                raise ValueError(f"is a link or shortcut, not a file: {a.path}")
            src = inside_roots(a.path, roots)
            if not src.is_file():
                raise ValueError(f"no longer exists: {a.path}")
            if same_file_key(src) in sources:
                raise ValueError(f"listed twice: {a.path}")
            sources.add(same_file_key(src))
            dst = None
            if a.op == "move":
                if not a.to:
                    raise ValueError(f"no destination for {a.path}")
                dst = inside_roots(a.to, roots)
                problem = path_problem(dst)
                if problem:
                    raise ValueError(problem)
                # Case-insensitive, as on Windows: "Finance/a.pdf" and "finance/A.pdf" collide.
                if dst.exists() or same_file_key(dst) in targets:
                    raise ValueError(f"destination already taken: {a.to}")
                targets.add(same_file_key(dst))
            resolved.append((a, src, dst))
        except (PathNotAllowed, ValueError) as exc:
            problems.append(str(exc))
    return resolved, problems


@router.post("/apply")
def apply(req: ApplyRequest):
    """Only called after the user approves actions from /plan. All actions are checked first;
    if any is invalid, nothing is applied. Moves are logged and can be undone as one batch;
    deletes go to the Recycle Bin."""
    if not req.actions:
        raise HTTPException(status_code=400, detail="No actions selected.")
    resolved, problems = _validate(req.actions, _roots())
    if problems:
        raise HTTPException(status_code=400, detail={"message": "Nothing was changed.", "problems": problems})

    batch = file_mover.new_batch()
    results = []
    for action, src, dst in resolved:
        try:
            if action.op == "move":
                file_mover.move(src, dst, settings.data_dir, batch)
                indexer.remove_file(src, settings.db_path, settings.vector_db_dir)
                indexer.index_file(dst, settings.db_path, settings.vector_db_dir)
            else:
                trash(src)
                file_mover.record_trash(src, settings.data_dir, batch)
                indexer.remove_file(src, settings.db_path, settings.vector_db_dir)
            results.append({"op": action.op, "path": str(src), "to": str(dst) if dst else None, "ok": True})
        except Exception as exc:
            results.append({"op": action.op, "path": str(src), "to": str(dst) if dst else None, "ok": False, "error": str(exc)})
    return {"batch": batch, "results": results}


@router.post("/undo")
def undo(req: UndoRequest):
    results = (
        file_mover.undo_batch(settings.data_dir, req.batch) if req.batch else file_mover.undo_last(settings.data_dir)
    )
    if results is None:
        raise HTTPException(status_code=404, detail="Nothing to undo.")
    for r in results:
        if r["ok"]:
            indexer.remove_file(r["from"], settings.db_path, settings.vector_db_dir)
            indexer.index_file(Path(r["to"]), settings.db_path, settings.vector_db_dir)
    return {"results": results}


@router.get("/history")
def history():
    return {"batches": file_mover.history(settings.data_dir)}
