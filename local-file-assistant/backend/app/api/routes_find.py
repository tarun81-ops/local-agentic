""""Find that file": describe a file, get it back. No chat model is used (see core/find_file.py)."""
import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core import find_file
from app.core.assistant import learning

router = APIRouter(prefix="/find", tags=["find"])


class PathRequest(BaseModel):
    path: str
    query: str = ""


# Plain def: SQLite and the embedding call block, so FastAPI runs this in its thread pool.
@router.get("")
def find(q: str, limit: int = 10):
    return find_file.find(q, limit=limit)


@router.get("/similar")
def similar(path: str, limit: int = 6):
    """Files like this one (similar name by sound or spelling, same folder, same type). Names only, instant."""
    _checked(path)
    return {"results": find_file.similar_files(path, limit=max(1, min(limit, 20)))}


def _checked(path: str) -> Path:
    if not find_file.allowed_to_open(path, folders=True):
        raise HTTPException(status_code=404, detail="That file isn't one the app can open (it may have moved, or PC access is off).")
    return Path(path)


@router.post("/open")
def open_file(req: PathRequest):
    """Opens a file Find returned in its default app (a folder opens in File Explorer). Only indexed files
    or files and folders PC access covers."""
    path = _checked(req.path)
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - checked above: an indexed or PC-access file, not a typed command
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
    try:
        learning.record_open(str(path), req.query)
    except Exception:
        pass  # learning must never stop a file from opening
    return {"opened": str(path)}


@router.post("/reveal")
def reveal(req: PathRequest):
    """Opens the file's folder in File Explorer with the file selected."""
    path = _checked(req.path)
    if sys.platform == "win32":
        # One argument string: explorer.exe parses "/select," itself; the path is quoted for spaces.
        subprocess.Popen(f'explorer.exe /select,"{path}"')  # noqa: S603 - path checked above
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path.parent)])
    return {"revealed": str(path)}
