"""Collections: searches the user saved (a query, optionally inside one folder). They are the
user's own data in assistant.db, so the learning wipe leaves them alone."""
import time

from app.db import assistant_db

NAME_MAX, QUERY_MAX = 60, 200
_COLS = "id, name, query, root, pinned, created_at"


def _row(r) -> dict:
    return {**{k: r[k] for k in ("id", "name", "query", "root", "created_at")}, "pinned": bool(r["pinned"])}


def _clean(name, query, root, *, required: bool) -> dict:
    out = {}
    if name is not None or required:
        name = " ".join((name or "").split())
        if not name or len(name) > NAME_MAX:
            raise ValueError(f"A name is 1 to {NAME_MAX} characters.")
        out["name"] = name
    if query is not None or required:
        query = " ".join((query or "").split())
        if not query or len(query) > QUERY_MAX:
            raise ValueError(f"A saved search needs a query of 1 to {QUERY_MAX} characters.")
        out["query"] = query
    if root is not None:
        out["root"] = root.strip() or None
    return out


def list_all() -> list[dict]:
    conn = assistant_db.connect()
    try:
        return [_row(r) for r in conn.execute(f"SELECT {_COLS} FROM collections ORDER BY pinned DESC, created_at DESC, id DESC")]
    finally:
        conn.close()


def get(coll_id: int) -> dict | None:
    conn = assistant_db.connect()
    try:
        r = conn.execute(f"SELECT {_COLS} FROM collections WHERE id = ?", (coll_id,)).fetchone()
        return _row(r) if r else None
    finally:
        conn.close()


def create(name: str, query: str, root: str | None = None, pinned: bool = True) -> dict:
    c = _clean(name, query, root, required=True)
    conn = assistant_db.connect()
    try:
        cur = conn.execute(
            "INSERT INTO collections(name, query, root, pinned, created_at) VALUES (?, ?, ?, ?, ?)",
            (c["name"], c["query"], c.get("root"), int(pinned), time.time()),
        )
        conn.commit()
        return get(cur.lastrowid)
    finally:
        conn.close()


def update(coll_id: int, name=None, query=None, root=None, pinned=None) -> bool:
    c = _clean(name, query, root, required=False)
    if pinned is not None:
        c["pinned"] = int(pinned)
    conn = assistant_db.connect()
    try:
        if c:
            sets = ", ".join(f"{k} = ?" for k in c)
            conn.execute(f"UPDATE collections SET {sets} WHERE id = ?", (*c.values(), coll_id))
            conn.commit()
        return conn.execute("SELECT 1 FROM collections WHERE id = ?", (coll_id,)).fetchone() is not None
    finally:
        conn.close()


def delete(coll_id: int) -> None:
    conn = assistant_db.connect()
    try:
        conn.execute("DELETE FROM collections WHERE id = ?", (coll_id,))
        conn.commit()
    finally:
        conn.close()
