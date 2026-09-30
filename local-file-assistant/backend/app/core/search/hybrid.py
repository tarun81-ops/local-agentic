import logging
from pathlib import Path

from app.config import settings
from app.core.search import fts_search, hybrid_ranker, vector_search
from app.db import sqlite_fts, vector_store

log = logging.getLogger(__name__)

SNIPPET_CHARS = 240


def snippet(text: str, query_terms: list[str], width: int = SNIPPET_CHARS) -> str:
    """A window of the chunk around the first query term, instead of the whole chunk."""
    flat = " ".join(text.split())
    if len(flat) <= width:
        return flat
    lower = flat.lower()
    hits = [i for i in (lower.find(t) for t in query_terms) if i >= 0]
    centre = min(hits) if hits else 0
    start = max(0, centre - width // 3)
    end = min(len(flat), start + width)
    start = max(0, end - width)
    piece = flat[start:end]
    if start > 0 and " " in piece:
        piece = "…" + piece[piece.find(" ") + 1 :]
    if end < len(flat) and " " in piece:
        piece = piece[: piece.rfind(" ")] + "…"
    return piece


def hybrid_search(q: str, root: str | None = None, limit: int = 6) -> dict:
    """Keyword + semantic search. Keyword search always runs; semantic search is skipped
    (not fatal) when Ollama's embedder is down, slow or nothing has been embedded yet."""
    conn = sqlite_fts.connect(settings.db_path)
    try:
        fts_results = fts_search.search(conn, q, limit=limit + 2, root=root)
        stale = sqlite_fts.vectors_stale(conn)  # schema upgrade pending a rebuild
    finally:
        conn.close()

    vector_results: list[dict] = []
    semantic_ok = False
    try:
        table = None if stale else vector_store.open_table(vector_store.connect(settings.vector_db_dir))
        if table is not None:
            vector_results = vector_search.search(table, q, limit=limit + 2, root=root)
            semantic_ok = True
    except Exception as exc:  # embedder unreachable/timeout, or a LanceDB error
        log.warning("semantic search skipped: %s", exc)

    if vector_results:
        merged = hybrid_ranker.merge(fts_results, vector_results, limit=limit)
    else:
        merged = [{**r, "match": "keyword"} for r in fts_results[:limit]]

    query_terms = fts_search.terms(q)
    for r in merged:
        r["name"] = Path(r["path"]).name
        r["snippet"] = snippet(r["text"], query_terms)
    return {"results": merged, "terms": query_terms, "semantic": semantic_ok}
