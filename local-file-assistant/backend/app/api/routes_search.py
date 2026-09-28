import httpx
from fastapi import APIRouter

from app.config import settings
from app.core.search import fts_search, hybrid_ranker, vector_search
from app.db.sqlite_fts import connect as fts_connect
from app.db.vector_store import connect as vector_connect

router = APIRouter(prefix="/search", tags=["search"])


def hybrid_search(q: str) -> list[dict]:
    conn = fts_connect(settings.db_path)
    try:
        fts_results = fts_search.search(conn, q)
    finally:
        conn.close()

    # Semantic search needs the embedding model in Ollama; keyword search must still
    # work when that's slow, unset up or not running (fresh install, nothing indexed yet).
    vector_results: list[dict] = []
    vdb = vector_connect(settings.vector_db_dir)
    if "chunks" in vdb.table_names():
        try:
            vector_results = vector_search.search(vdb.open_table("chunks"), q)
        except httpx.HTTPError:
            pass

    return hybrid_ranker.merge(fts_results, vector_results) if vector_results else fts_results


@router.get("")
async def search(q: str):
    return {"results": hybrid_search(q)}
