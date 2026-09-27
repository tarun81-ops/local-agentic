import sqlite3


def _sanitize(query: str) -> str:
    """Quote each token so FTS5 operator syntax in user input can't raise an error."""
    return " ".join(f'"{t}"' for t in query.split())


def search(conn: sqlite3.Connection, query: str, limit: int = 5) -> list[dict]:
    rows = conn.execute(
        "SELECT path, chunk_no, text, bm25(chunks) AS score "
        "FROM chunks WHERE chunks MATCH ? ORDER BY score LIMIT ?",
        (_sanitize(query), limit),
    ).fetchall()
    return [{"path": r[0], "chunk_no": r[1], "text": r[2], "score": r[3]} for r in rows]
