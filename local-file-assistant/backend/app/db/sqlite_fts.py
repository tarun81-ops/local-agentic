import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    hash TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    path, chunk_no UNINDEXED, text
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def file_hash_matches(conn: sqlite3.Connection, path: Path, file_hash: str) -> bool:
    row = conn.execute("SELECT hash FROM files WHERE path = ?", (str(path),)).fetchone()
    return row is not None and row[0] == file_hash


def upsert_file(conn: sqlite3.Connection, path: Path, file_hash: str, chunks: list[tuple[int, str]]) -> None:
    conn.execute("DELETE FROM chunks WHERE path = ?", (str(path),))
    conn.executemany(
        "INSERT INTO chunks (path, chunk_no, text) VALUES (?, ?, ?)",
        [(str(path), chunk_no, text) for chunk_no, text in chunks],
    )
    conn.execute(
        "INSERT INTO files (path, hash) VALUES (?, ?) "
        "ON CONFLICT(path) DO UPDATE SET hash = excluded.hash",
        (str(path), file_hash),
    )
    conn.commit()
