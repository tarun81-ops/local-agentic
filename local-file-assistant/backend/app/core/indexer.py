import hashlib
from pathlib import Path

from app.core.parsers.docx_parser import parse_docx
from app.core.parsers.pdf_parser import parse_pdf
from app.core.search.vector_search import embed
from app.db.sqlite_fts import connect, file_hash_matches, upsert_file
from app.db.vector_store import connect as vconnect
from app.db.vector_store import get_or_create_table, upsert as vupsert

PARSERS = {".pdf": parse_pdf, ".docx": parse_docx}


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def index_folder(folder: Path, db_path: Path, vector_db_dir: Path) -> dict:
    conn = connect(db_path)
    vdb = vconnect(vector_db_dir)
    table = None
    indexed, skipped = 0, 0
    for path in folder.rglob("*"):
        parser = PARSERS.get(path.suffix.lower())
        if parser is None or not path.is_file():
            continue
        file_hash = _hash_file(path)
        if file_hash_matches(conn, path, file_hash):
            skipped += 1
            continue
        chunks = parser(path)
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
        indexed += 1
    conn.close()
    return {"indexed": indexed, "skipped": skipped}
