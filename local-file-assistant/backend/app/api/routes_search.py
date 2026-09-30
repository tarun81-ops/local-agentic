import time

from fastapi import APIRouter

from app.core.search.hybrid import hybrid_search

router = APIRouter(prefix="/search", tags=["search"])


# Plain def, not async: SQLite and the embedding call block, so FastAPI runs this in its
# thread pool instead of stalling every other request on the event loop.
@router.get("")
def search(q: str, root: str | None = None, limit: int = 10):
    t0 = time.perf_counter()
    out = hybrid_search(q, root=root, limit=min(max(limit, 1), 25))
    out["ms"] = round((time.perf_counter() - t0) * 1000)
    return out
