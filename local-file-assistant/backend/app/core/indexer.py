import hashlib
from pathlib import Path
from typing import Callable

from app.core.parsers.docx_parser import parse_docx
from app.core.parsers.pdf_parser import parse_pdf
from app.core.search.vector_search import embed
from app.db.sqlite_fts import connect, file_hash_matches, upsert_file
from app.db.vector_store import connect as vconnect
from app.db.vector_store import get_or_create_table, upsert as vupsert

PARSERS = {".pdf": parse_pdf, ".docx": parse_docx}


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _index_one(path: Path, conn, vdb, table):
    """Returns (status, table) — table is the lazily created/opened LanceDB table handle,
    threaded through calls so it's only looked up once per run."""
    file_hash = _hash_file(path)
    if file_hash_matches(conn, path, file_hash):
        return "skipped", table
    chunks = PARSERS[path.suffix.lower()](path)
    upsert_file(conn, path, file_hash, chunks)
    if chunks:
        vectors = embed([text for _, text in chunks])
        if table is None:
            table = get_or_create_table(vdb, dim=len(vectors[0]))
        rows = [
            {"path": str(path), "chunk_no": n, "text": t, "vector": v}
            for (n, t), v in zip(chunks, vectors)
        ]
        vupsert(table, str(path), rows)
    return "indexed", table


def index_file(path: Path, db_path: Path, vector_db_dir: Path) -> str:
    """Indexes a single file, opening its own connections. Used by the folder watcher,
    where events arrive one at a time from a background thread."""
    conn = connect(db_path)
    vdb = vconnect(vector_db_dir)
    try:
        status, _ = _index_one(path, conn, vdb, None)
    except Exception:
        status = "failed"
    finally:
        conn.close()
    return status


def index_folder(
    folder: Path,
    db_path: Path,
    vector_db_dir: Path,
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    conn = connect(db_path)
    vdb = vconnect(vector_db_dir)
    table = None
    seen = indexed = skipped = failed = 0
    for path in folder.rglob("*"):
        if path.suffix.lower() not in PARSERS or not path.is_file():
            continue
        seen += 1
        try:
            status, table = _index_one(path, conn, vdb, table)
        except Exception:
            status = "failed"
        if status == "indexed":
            indexed += 1
        elif status == "skipped":
            skipped += 1
        else:
            failed += 1
        if on_progress:
            on_progress({"seen": seen, "indexed": indexed, "skipped": skipped, "failed": failed})
    conn.close()
    return {"seen": seen, "indexed": indexed, "skipped": skipped, "failed": failed}
