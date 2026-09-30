import time

from fastapi import APIRouter

from app.core.search.hybrid import hybrid_search

router = APIRouter(prefix="/search", tags=["search"])


# Plain def, not async: SQLite and the embedding call block, so FastAPI runs this in its
# thread pool instead of stalling every other request on the event loop.
@router.get("")
def search(q: str, root: str | None = None, limit: int = 10):
    """One result per file (its best-ranked chunk): this finds documents; chat reads chunks."""
    t0 = time.perf_counter()
    n = min(max(limit, 1), 25)
    out = hybrid_search(q, root=root, limit=n * 3)  # extra chunks, since several can share a file
    best: dict[str, dict] = {}
    for r in out["results"]:
        best.setdefault(r["path"], r)
    out["results"] = list(best.values())[:n]
    out["ms"] = round((time.perf_counter() - t0) * 1000)
    return out
