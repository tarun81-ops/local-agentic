"""What the app learns from use. Two small, local signals (assistant.db, wipe() clears them):
which file you open after which search (a bounded nudge to search ranking), and which answers
you mark helpful or not (not-helpful questions can be exported as eval cases)."""
import json
import time

from app.config import settings
from app.core.search import fts_search
from app.db import assistant_db

HISTORY = 500  # most recent opens considered
SIMILAR = 0.5  # share of query words two searches must have in common to count as "similar"


def record_open(path: str, query: str = "") -> None:
    conn = assistant_db.connect()
    try:
        conn.execute("INSERT INTO file_opens(path, query, opened_at) VALUES (?, ?, ?)", (path, (query or "").strip()[:200], time.time()))
        conn.commit()
    finally:
        conn.close()


def open_prior(query: str, paths) -> dict[str, float]:
    """How many positions each path may move up for this search: from 0 to open_prior_max_shift.
    Half a position per similar past search that ended in opening it, so a single stray open
    changes nothing and two similar ones move a file up one place (four or more: the cap)."""
    cap = settings.open_prior_max_shift
    wanted = set(paths)
    terms = set(fts_search.terms(query))
    if cap <= 0 or not terms or not wanted:
        return {}
    conn = assistant_db.connect()
    try:
        rows = conn.execute("SELECT path, query FROM file_opens ORDER BY id DESC LIMIT ?", (HISTORY,)).fetchall()
    finally:
        conn.close()
    weight: dict[str, float] = {}
    for row in rows:
        if row["path"] not in wanted:
            continue
        past = set(fts_search.terms(row["query"]))
        overlap = len(terms & past) / len(terms | past) if past else 0.0
        if overlap >= SIMILAR:
            weight[row["path"]] = weight.get(row["path"], 0.0) + overlap
    return {p: min(float(cap), 0.5 * w) for p, w in weight.items()}


def recent_opens(limit: int = 8) -> list[dict]:
    """The files opened most recently, one row per file, newest first. Derived from file_opens,
    so wipe() clears it along with the rest of what is learned from use."""
    conn = assistant_db.connect()
    try:
        rows = conn.execute("SELECT path, MAX(opened_at) AS at FROM file_opens GROUP BY path ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
        return [{"path": r["path"], "at": r["at"]} for r in rows]
    finally:
        conn.close()


def set_feedback(msg_id: int, rating: int) -> None:
    """1 helpful, -1 not helpful, 0 takes the mark back. Only assistant answers can be rated."""
    if rating not in (-1, 0, 1):
        raise ValueError("The rating must be 1, -1 or 0.")
    conn = assistant_db.connect()
    try:
        if conn.execute("SELECT 1 FROM messages WHERE id = ? AND role = 'assistant'", (msg_id,)).fetchone() is None:
            raise LookupError(msg_id)
        if rating == 0:
            conn.execute("DELETE FROM feedback WHERE msg_id = ?", (msg_id,))
        else:
            conn.execute("INSERT OR REPLACE INTO feedback(msg_id, rating, created_at) VALUES (?, ?, ?)", (msg_id, rating, time.time()))
        conn.commit()
    finally:
        conn.close()


def export_questions() -> list[dict]:
    """The questions whose answers were marked not helpful, in the eval question format, plus what
    was answered and from which files so the expected answer and file can be filled in."""
    conn = assistant_db.connect()
    try:
        rows = conn.execute(
            "SELECT m.id, m.conv_id, m.content, m.meta_json, f.created_at FROM feedback f JOIN messages m ON m.id = f.msg_id "
            "WHERE f.rating = -1 ORDER BY f.created_at"
        ).fetchall()
        out = []
        for r in rows:
            q = conn.execute("SELECT content FROM messages WHERE conv_id = ? AND id < ? AND role = 'user' ORDER BY id DESC LIMIT 1", (r["conv_id"], r["id"])).fetchone()
            if q is None:
                continue
            cites = json.loads(r["meta_json"]).get("citations", [])
            files = list(dict.fromkeys(c["file"] for c in cites if c.get("file")))
            out.append({"q": q["content"], "note": "marked not helpful; add expect/file once you know the right answer", "answer": r["content"][:300], "files": files})
        return out
    finally:
        conn.close()


def stats() -> dict:
    conn = assistant_db.connect()
    try:
        return {
            "opens": conn.execute("SELECT COUNT(*) FROM file_opens").fetchone()[0],
            "helpful": conn.execute("SELECT COUNT(*) FROM feedback WHERE rating = 1").fetchone()[0],
            "not_helpful": conn.execute("SELECT COUNT(*) FROM feedback WHERE rating = -1").fetchone()[0],
        }
    finally:
        conn.close()


def wipe() -> dict:
    conn = assistant_db.connect()
    try:
        out = {"opens": conn.execute("DELETE FROM file_opens").rowcount, "ratings": conn.execute("DELETE FROM feedback").rowcount}
        conn.commit()
        return out
    finally:
        conn.close()
