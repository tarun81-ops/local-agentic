def merge(fts_results: list[dict], vector_results: list[dict], k: int = 60, limit: int = 6) -> list[dict]:
    """Reciprocal Rank Fusion: combines two ranked lists by rank position only, so bm25 and
    vector distances never need to be normalised against each other. Each result is tagged
    with how it matched: "keyword", "semantic" or "both"."""
    scores: dict[tuple[str, int], float] = {}
    rows: dict[tuple[str, int], dict] = {}
    seen_in: dict[tuple[str, int], set[str]] = {}
    for kind, results in (("keyword", fts_results), ("semantic", vector_results)):
        for rank, r in enumerate(results):
            key = (r["path"], r["chunk_no"])
            scores[key] = scores.get(key, 0.0) + 1 / (k + rank + 1)
            rows.setdefault(key, r)
            seen_in.setdefault(key, set()).add(kind)
    ranked = sorted(scores, key=scores.get, reverse=True)[:limit]
    out = []
    for key in ranked:
        kinds = seen_in[key]
        out.append({**rows[key], "match": "both" if len(kinds) == 2 else next(iter(kinds))})
    return out
