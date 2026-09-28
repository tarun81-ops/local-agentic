from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.core.indexer import index_folder

router = APIRouter(prefix="/files", tags=["files"])

# ponytail: single global status dict — this is a single-user local app with one indexer
# running at a time, not a job queue. Add per-job IDs if concurrent index runs are ever needed.
_status = {"state": "idle", "seen": 0, "indexed": 0, "skipped": 0, "failed": 0, "error": None}


class IndexRequest(BaseModel):
    folder: str


def _run_index(folder: Path) -> None:
    _status.update(state="running", seen=0, indexed=0, skipped=0, failed=0, error=None)
    try:
        result = index_folder(folder, settings.db_path, settings.vector_db_dir, on_progress=_status.update)
        _status.update(state="done", **result)
    except Exception as exc:
        _status.update(state="error", error=str(exc))


@router.post("/index")
async def start_index(req: IndexRequest, background_tasks: BackgroundTasks):
    folder = Path(req.folder)
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail="folder does not exist or is not a directory")
    if _status["state"] == "running":
        raise HTTPException(status_code=409, detail="an index run is already in progress")
    background_tasks.add_task(_run_index, folder)
    return {"state": "started", "folder": str(folder)}


@router.get("/index/status")
async def status():
    return _status
