def merge(fts_results: list[dict], vector_results: list[dict], k: int = 60, limit: int = 5) -> list[dict]:
    """Reciprocal Rank Fusion: combines two ranked lists using rank position only, so FTS5's
    bm25 scores and LanceDB's distance scores never need to be normalized against each other."""
    scores: dict[tuple[str, int], float] = {}
    rows: dict[tuple[str, int], dict] = {}
    for results in (fts_results, vector_results):
        for rank, r in enumerate(results):
            key = (r["path"], r["chunk_no"])
            scores[key] = scores.get(key, 0.0) + 1 / (k + rank + 1)
            rows[key] = r
    ranked = sorted(scores, key=scores.get, reverse=True)[:limit]
    return [rows[key] for key in ranked]
