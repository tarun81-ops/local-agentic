import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# Bump when the schema changes. An older index is dropped and rebuilt on the next scan,
# which is cheap next to keeping migration code for a single-user local cache.
# 3: vectors now embed the file name and folder too (indexer.embed_text), so the old ones go.
# Porter stemming (tokenize = 'porter unicode61') was measured with eval/run_eval.py --retrieval
# and cost a question without winning one; retry it against real documents before adding it.
SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS roots (
    path TEXT PRIMARY KEY,
    added_at TEXT NOT NULL,
    last_scan TEXT
);
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    root TEXT NOT NULL,
    hash TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS files_root ON files(root);
CREATE INDEX IF NOT EXISTS files_hash ON files(hash);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    path, loc_kind UNINDEXED, loc_no UNINDEXED, chunk_no UNINDEXED, text
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL lets the watcher thread and an index run write while searches keep reading.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    reset = conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION
    if reset:
        conn.executescript("DROP TABLE IF EXISTS chunks; DROP TABLE IF EXISTS files;")
    conn.executescript(SCHEMA)
    if reset:
        with conn:
            # Folders are kept but marked unscanned, so the app rebuilds them on startup, and
            # the old vectors are flagged: they'd otherwise outlive files the rebuild never sees.
            conn.execute("UPDATE roots SET last_scan = NULL")
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('vectors_stale', '1')")
        # Last, so a crash part-way through simply repeats the upgrade next time.
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    return conn


def vectors_stale(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM meta WHERE key = 'vectors_stale'").fetchone()
    return row is not None and row[0] == "1"


def clear_vectors_stale(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute("DELETE FROM meta WHERE key = 'vectors_stale'")


# ---- files and chunks -------------------------------------------------------------

def file_hash_matches(conn: sqlite3.Connection, path: Path, file_hash: str) -> bool:
    row = conn.execute("SELECT hash FROM files WHERE path = ?", (str(path),)).fetchone()
    return row is not None and row[0] == file_hash


def file_unchanged(conn: sqlite3.Connection, path: Path, size: int, mtime: float) -> bool:
    """Same size and modification time as when it was fully indexed: skip without re-reading.
    A blank hash means the last attempt had no vectors, so that file is always retried."""
    row = conn.execute("SELECT hash, size, mtime FROM files WHERE path = ?", (str(path),)).fetchone()
    return row is not None and row[0] != "" and row[1] == size and abs(row[2] - mtime) < 1e-6


def upsert_file(conn, path: Path, root: Path, file_hash: str, size: int, mtime: float, chunks) -> None:
    """chunks: iterable of (chunk_no, Chunk)."""
    p = str(path)
    with conn:
        conn.execute("DELETE FROM chunks WHERE path = ?", (p,))
        conn.executemany(
            "INSERT INTO chunks (path, loc_kind, loc_no, chunk_no, text) VALUES (?, ?, ?, ?, ?)",
            [(p, c.loc_kind, c.loc_no, n, c.text) for n, c in chunks],
        )
        conn.execute(
            "INSERT INTO files (path, root, hash, size, mtime, indexed_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET root=excluded.root, hash=excluded.hash, size=excluded.size, "
            "mtime=excluded.mtime, indexed_at=excluded.indexed_at",
            (p, str(root), file_hash, size, mtime, _now()),
        )


def delete_file(conn: sqlite3.Connection, path: Path | str) -> None:
    with conn:
        conn.execute("DELETE FROM chunks WHERE path = ?", (str(path),))
        conn.execute("DELETE FROM files WHERE path = ?", (str(path),))


def indexed_paths(conn: sqlite3.Connection, root: Path | str | None = None) -> list[str]:
    if root is None:
        return [r[0] for r in conn.execute("SELECT path FROM files")]
    return [r[0] for r in conn.execute("SELECT path FROM files WHERE root = ?", (str(root),))]


def is_indexed(conn: sqlite3.Connection, path: Path | str) -> bool:
    return conn.execute("SELECT 1 FROM files WHERE path = ?", (str(path),)).fetchone() is not None


def first_text(conn: sqlite3.Connection, path: str, chars: int = 200) -> str:
    row = conn.execute("SELECT text FROM chunks WHERE path = ? AND chunk_no = 0", (path,)).fetchone()
    return (row[0] or "")[:chars] if row else ""


def list_files(conn: sqlite3.Connection, root: str | None = None, limit: int = 5000) -> list[dict]:
    sql = "SELECT path, root, hash, size, mtime FROM files"
    args: tuple = ()
    if root:
        sql += " WHERE root = ?"
        args = (root,)
    sql += " ORDER BY path LIMIT ?"
    return [dict(r) for r in conn.execute(sql, (*args, limit))]


# ---- roots ------------------------------------------------------------------------

def add_root(conn: sqlite3.Connection, root: Path) -> None:
    with conn:
        conn.execute("INSERT OR IGNORE INTO roots (path, added_at) VALUES (?, ?)", (str(root), _now()))


def remove_root(conn: sqlite3.Connection, root: str) -> list[str]:
    """Forgets a folder and its index entries (never touches the files). Returns removed paths."""
    paths = indexed_paths(conn, root)
    with conn:
        conn.execute("DELETE FROM chunks WHERE path IN (SELECT path FROM files WHERE root = ?)", (root,))
        conn.execute("DELETE FROM files WHERE root = ?", (root,))
        conn.execute("DELETE FROM roots WHERE path = ?", (root,))
    return paths


def unscanned_roots(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT path FROM roots WHERE last_scan IS NULL ORDER BY added_at")]


def mark_scanned(conn: sqlite3.Connection, root: Path) -> None:
    with conn:
        conn.execute("UPDATE roots SET last_scan = ? WHERE path = ?", (_now(), str(root)))


def list_roots(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT r.path, r.added_at, r.last_scan, COUNT(f.path) AS files, COALESCE(SUM(f.size), 0) AS bytes "
        "FROM roots r LEFT JOIN files f ON f.root = r.path GROUP BY r.path ORDER BY r.added_at"
    )
    return [dict(r) for r in rows]


def root_paths(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT path FROM roots")]


def root_for(conn: sqlite3.Connection, path: Path) -> str | None:
    """The indexed folder that contains path, if any (longest match wins for nested folders)."""
    best = None
    for root in root_paths(conn):
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if best is None or len(root) > len(best):
            best = root
    return best


def duplicate_groups(conn: sqlite3.Connection, root: str | None = None) -> list[list[dict]]:
    """Files with identical content (same sha256), oldest first in each group."""
    sql = "SELECT path, root, hash, size, mtime FROM files WHERE hash != ''"  # '' = retry pending
    args: tuple = ()
    if root:
        sql += " AND root = ?"
        args = (root,)
    groups: dict[str, list[dict]] = {}
    for r in conn.execute(sql, args):
        groups.setdefault(r["hash"], []).append(dict(r))
    return [sorted(g, key=lambda f: f["mtime"]) for g in groups.values() if len(g) > 1]
