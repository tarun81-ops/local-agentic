"""Index schema upgrade (v1 -> v2), fast rescans and cancelling a scan."""
import sqlite3

from app.core import indexer
from app.db import sqlite_fts, vector_store

V1_SCHEMA = """
CREATE TABLE roots (path TEXT PRIMARY KEY, added_at TEXT NOT NULL, last_scan TEXT);
CREATE TABLE files (path TEXT PRIMARY KEY, root TEXT NOT NULL, hash TEXT NOT NULL, mtime REAL NOT NULL);
CREATE VIRTUAL TABLE chunks USING fts5(path, page UNINDEXED, text);
PRAGMA user_version=1;
"""


def _make_v1(db, root, stale_path):
    conn = sqlite3.connect(db)
    conn.executescript(V1_SCHEMA)
    conn.execute("INSERT INTO roots VALUES (?, '2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00')", (str(root),))
    conn.execute("INSERT INTO files VALUES (?, ?, 'abc', 1.0)", (stale_path, str(root)))
    conn.execute("INSERT INTO chunks VALUES (?, 1, 'old text')", (stale_path,))
    conn.commit()
    conn.close()


def test_v1_index_upgrades_and_rebuilds_cleanly(corpus, db_paths):
    db, vdb = db_paths
    stale = str(corpus / "deleted_long_ago.pdf")
    _make_v1(db, corpus.resolve(), stale)
    # Vectors written by the old version, including one for a file that no longer exists.
    table = vector_store.get_or_create_table(vector_store.connect(vdb), dim=64)
    table.add([{"path": stale, "root": str(corpus), "loc_kind": "page", "loc_no": 1, "chunk_no": 0, "text": "old", "vector": [0.1] * 64}])

    conn = sqlite_fts.connect(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == sqlite_fts.SCHEMA_VERSION
        assert sqlite_fts.indexed_paths(conn) == []  # old rows dropped
        assert sqlite_fts.unscanned_roots(conn) == [str(corpus.resolve())]  # folder kept, marked for rebuild
        assert sqlite_fts.vectors_stale(conn)
    finally:
        conn.close()

    counts = indexer.index_folder(corpus, db, vdb)
    assert counts["indexed"] == 2 and not counts["cancelled"]

    conn = sqlite_fts.connect(db)
    try:
        assert not sqlite_fts.vectors_stale(conn)
        assert sqlite_fts.unscanned_roots(conn) == []
        sqlite_paths = set(sqlite_fts.indexed_paths(conn))
    finally:
        conn.close()
    table = vector_store.open_table(vector_store.connect(vdb))
    vector_paths = set(table.to_arrow().column("path").to_pylist())
    assert stale not in vector_paths
    assert vector_paths == sqlite_paths  # both indexes describe the same files


def test_second_connect_does_not_reset_again(db_paths):
    db, _ = db_paths
    sqlite_fts.connect(db).close()
    conn = sqlite_fts.connect(db)
    sqlite_fts.clear_vectors_stale(conn)
    conn.close()
    conn = sqlite_fts.connect(db)
    try:
        assert not sqlite_fts.vectors_stale(conn)
    finally:
        conn.close()


def test_unchanged_files_are_skipped_without_hashing(corpus, db_paths, monkeypatch):
    db, vdb = db_paths
    indexer.index_folder(corpus, db, vdb)
    hashed = []
    real = indexer._hash_file
    monkeypatch.setattr(indexer, "_hash_file", lambda p: (hashed.append(p), real(p))[1])
    counts = indexer.index_folder(corpus, db, vdb)
    assert counts["skipped"] == 2 and hashed == []


def test_cancel_stops_without_pruning_or_marking_scanned(corpus, db_paths):
    db, vdb = db_paths
    indexer.index_folder(corpus, db, vdb)
    (corpus / "invoice_notes.pdf").unlink()
    indexer.CANCEL.set()
    try:
        counts = indexer.index_folder(corpus, db, vdb)
    finally:
        indexer.CANCEL.clear()
    assert counts["cancelled"] and counts["removed"] == 0
    conn = sqlite_fts.connect(db)
    try:
        assert len(sqlite_fts.indexed_paths(conn)) == 2  # nothing forgotten on a partial walk
    finally:
        conn.close()
