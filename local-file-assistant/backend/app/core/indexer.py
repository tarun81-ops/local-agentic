import hashlib
import logging
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.core import ram
from app.core.parsers import PARSERS
from app.core.search.vector_search import embed
from app.db import sqlite_fts, vector_store

log = logging.getLogger(__name__)

# The watcher thread and a folder scan can index at the same time; one file at a time keeps
# the SQLite and LanceDB rows for a file consistent with each other.
WRITE_LOCK = threading.Lock()

# Set to stop the folder scan in progress after the current file (Index page > STOP).
CANCEL = threading.Event()

# Most recent failures, shown on the Index page so a bad file can actually be diagnosed.
RECENT_ERRORS: deque = deque(maxlen=20)


def is_indexable(path: Path) -> bool:
    return path.suffix.lower() in PARSERS and not path.name.startswith("~$")  # ~$ = Office lock files


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _record_error(path: Path, exc: Exception) -> None:
    log.error("failed to index %s: %s", path, exc, exc_info=not isinstance(exc, RuntimeError))
    RECENT_ERRORS.appendleft(
        {
            "path": str(path),
            "error": f"{type(exc).__name__}: {exc}",
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )


class _Run:
    """State shared across one indexing pass: DB handles, the lazily opened vector table,
    and whether the embedder is reachable (after one failure the rest of the pass is
    keyword-only instead of waiting on a timeout for every file)."""

    def __init__(self, db_path: Path, vector_db_dir: Path):
        self.conn = sqlite_fts.connect(db_path)
        self.vdb = vector_store.connect(vector_db_dir)
        if sqlite_fts.vectors_stale(self.conn):
            # The keyword index was rebuilt for a new schema; start the vectors over with it.
            with WRITE_LOCK:
                vector_store.drop_table(self.vdb)
                sqlite_fts.clear_vectors_stale(self.conn)
        self.table = vector_store.open_table(self.vdb)
        self.embed_ok = True
        self.low_memory_noted = False
        self.keyword_only = 0  # files indexed without vectors in this pass (embedder down, low RAM)

    def close(self):
        self.conn.close()


def embed_text(path: Path, root: Path, text: str) -> str:
    """What the embedder sees for a chunk: its file name and folder, then the text. "My car
    insurance" can then find a policy whose text says "motor" and never "car". The stored and
    cited chunk text stays the text alone."""
    try:
        folder = path.parent.relative_to(root) if path.parent != root else Path(root.name)
    except ValueError:
        folder = Path(path.parent.name)
    return f"File: {path.name}\nFolder: {folder.as_posix()}\n\n{text}"


def _index_one(path: Path, root: Path, run: _Run) -> str:
    stat = path.stat()
    # Cheap check first: hashing reads the whole file, which dominates rescans of big folders.
    if sqlite_fts.file_unchanged(run.conn, path, stat.st_size, stat.st_mtime):
        return "skipped"
    file_hash = _hash_file(path)
    if sqlite_fts.file_hash_matches(run.conn, path, file_hash):
        return "skipped"
    chunks = list(enumerate(PARSERS[path.suffix.lower()](path)))

    vectors = None
    if chunks and run.embed_ok and ram.is_low():
        # Keyword-indexed now; the blank hash below makes the next scan add the vectors.
        if not run.low_memory_noted:
            run.low_memory_noted = True
            free = ram.status()["free_mb"]
            _record_error(path, RuntimeError(f"low memory ({free} MB free): keyword index only for now, vectors added on the next scan"))
    elif chunks and run.embed_ok:
        try:
            vectors = embed([embed_text(path, root, c.text) for _, c in chunks])
        except Exception as exc:
            run.embed_ok = False
            _record_error(path, RuntimeError(f"embedding model unreachable, keyword index only ({exc})"))

    with WRITE_LOCK:
        # The hash goes in last (mark_complete), after the vectors: until then the file counts
        # as unfinished, so a failure or crash anywhere before that is retried on the next scan.
        sqlite_fts.upsert_file(run.conn, path, root, stat.st_size, stat.st_mtime, chunks)
        if vectors is not None:
            if run.table is None:
                run.table = vector_store.get_or_create_table(run.vdb, dim=len(vectors[0]))
            rows = [
                {
                    "path": str(path),
                    "root": str(root),
                    "loc_kind": c.loc_kind,
                    "loc_no": c.loc_no,
                    "chunk_no": n,
                    "text": c.text,
                    "vector": v,
                }
                for (n, c), v in zip(chunks, vectors, strict=True)
            ]
            vector_store.upsert(run.table, str(path), rows)
        elif run.table is not None:
            vector_store.delete_path(run.table, str(path))
        if vectors is not None or not chunks:
            sqlite_fts.mark_complete(run.conn, path, file_hash)
        else:
            run.keyword_only += 1  # searchable by keyword; vectors on the next scan
    return "indexed"


def _forget(path: str, run: _Run) -> None:
    with WRITE_LOCK:
        sqlite_fts.delete_file(run.conn, path)
        if run.table is not None:
            vector_store.delete_path(run.table, path)


def index_folder(
    folder: Path,
    db_path: Path,
    vector_db_dir: Path,
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    """Indexes new/changed files under folder and removes index entries for files that
    no longer exist there."""
    folder = folder.resolve()
    run = _Run(db_path, vector_db_dir)
    counts = {"seen": 0, "indexed": 0, "skipped": 0, "failed": 0, "removed": 0, "keyword_only": 0}
    cancelled = False
    try:
        sqlite_fts.add_root(run.conn, folder)
        present: set[str] = set()
        for path in folder.rglob("*"):
            if CANCEL.is_set():
                cancelled = True
                break
            if not is_indexable(path) or not path.is_file():
                continue
            present.add(str(path))
            counts["seen"] += 1
            try:
                status = _index_one(path, folder, run)
            except Exception as exc:
                _record_error(path, exc)
                status = "failed"
            counts[status] += 1
            counts["keyword_only"] = run.keyword_only
            if on_progress:
                on_progress(dict(counts))

        if not cancelled:
            # Only a complete walk knows which files are really gone.
            for stale in set(sqlite_fts.indexed_paths(run.conn, folder)) - present:
                _forget(stale, run)
                counts["removed"] += 1
            sqlite_fts.mark_scanned(run.conn, folder)
    finally:
        run.close()
    counts["semantic"] = run.embed_ok and run.keyword_only == 0  # every file got its vectors
    counts["cancelled"] = cancelled
    return counts


def index_file(path: Path, db_path: Path, vector_db_dir: Path, root: Path | None = None) -> str:
    """Indexes one file (used by the folder watcher). root defaults to the indexed folder
    that contains the file."""
    run = _Run(db_path, vector_db_dir)
    try:
        if root is None:
            found = sqlite_fts.root_for(run.conn, path)
            if found is None:
                return "ignored"
            root = Path(found)
        return _index_one(path, root, run)
    except Exception as exc:
        _record_error(path, exc)
        return "failed"
    finally:
        run.close()


def remove_file(path: Path | str, db_path: Path, vector_db_dir: Path) -> None:
    """Drops a deleted or moved-away file from both indexes."""
    run = _Run(db_path, vector_db_dir)
    try:
        _forget(str(path), run)
    finally:
        run.close()


def remove_root(root: str, db_path: Path, vector_db_dir: Path) -> int:
    """Stops indexing a folder and forgets its entries. Files on disk are untouched."""
    run = _Run(db_path, vector_db_dir)
    try:
        with WRITE_LOCK:
            paths = sqlite_fts.remove_root(run.conn, root)
            if run.table is not None:
                for p in paths:
                    vector_store.delete_path(run.table, p)
        return len(paths)
    finally:
        run.close()
