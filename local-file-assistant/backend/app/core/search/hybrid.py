import logging
from pathlib import Path

from app.config import settings
from app.core.assistant import learning
from app.core.search import fts_search, hybrid_ranker, reranker, vector_search
from app.db import sqlite_fts, vector_store

log = logging.getLogger(__name__)

SNIPPET_CHARS = 240
RERANK_POOL = 20  # candidates the cross-encoder re-reads; its cost grows with this


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


def _apply_open_prior(q: str, rows: list[dict]) -> list[dict]:
    """Moves files the user habitually opens for similar searches up a few places (bounded by
    open_prior_max_shift). Learning is a bonus: any failure leaves the ranking untouched."""
    try:
        boosts = learning.open_prior(q, {r["path"] for r in rows})
    except Exception as exc:
        log.warning("open-history ranking skipped: %s", exc)
        return rows
    if not boosts:
        return rows
    order = sorted(range(len(rows)), key=lambda i: i - boosts.get(rows[i]["path"], 0.0) - (1e-6 if rows[i]["path"] in boosts else 0.0))  # a boost wins ties
    return [rows[i] for i in order]


def hybrid_search(q: str, root: str | None = None, limit: int = 6) -> dict:
    """Keyword + semantic search, then (when a model is installed) a cross-encoder re-ranks the
    top RERANK_POOL. Keyword search always runs; semantic search and re-ranking are skipped
    (not fatal) when their model is missing, down or slow."""
    rerank_on = reranker.available()
    # A wider pool lets a habitually opened file rise into view even when the re-ranker is off.
    pool = max(limit, RERANK_POOL) if (rerank_on or settings.open_prior_max_shift > 0) else limit
    conn = sqlite_fts.connect(settings.db_path)
    try:
        fts_results = fts_search.search(conn, q, limit=pool + 2, root=root)
        stale = sqlite_fts.vectors_stale(conn)  # schema upgrade pending a rebuild
    finally:
        conn.close()

    vector_results: list[dict] = []
    semantic_ok = False
    try:
        table = None if stale else vector_store.open_table(vector_store.connect(settings.vector_db_dir))
        if table is not None:
            vector_results = vector_search.search(table, q, limit=pool + 2, root=root)
            semantic_ok = True
    except Exception as exc:  # embedder unreachable/timeout, or a LanceDB error
        log.warning("semantic search skipped: %s", exc)

    if vector_results:
        merged = hybrid_ranker.merge(fts_results, vector_results, limit=pool)
    else:
        merged = [{**r, "match": "keyword"} for r in fts_results[:pool]]

    reranked = False
    if rerank_on and len(merged) > 1:
        try:
            merged = reranker.rerank(q, merged[:RERANK_POOL]) + merged[RERANK_POOL:]
            reranked = True
        except Exception as exc:  # a broken model file must not break search
            log.warning("re-ranking skipped: %s", exc)
    merged = _apply_open_prior(q, merged)[:limit]

    query_terms = fts_search.terms(q)
    for r in merged:
        r["name"] = Path(r["path"]).name
        r["snippet"] = snippet(r["text"], query_terms)
    return {"results": merged, "terms": query_terms, "semantic": semantic_ok, "reranked": reranked}
