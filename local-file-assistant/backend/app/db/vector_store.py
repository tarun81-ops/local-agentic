from pathlib import Path

import lancedb
import pyarrow as pa

TABLE_NAME = "chunks"
_FIELDS = {"path", "root", "loc_kind", "loc_no", "chunk_no", "text", "vector"}


def connect(db_dir: Path):
    db_dir.mkdir(parents=True, exist_ok=True)
    return lancedb.connect(db_dir)


def _table_names(db) -> list[str]:
    names = db.list_tables() if hasattr(db, "list_tables") else db.table_names()
    return list(getattr(names, "tables", names))


def open_table(db):
    """The chunks table, or None if nothing has been embedded yet."""
    if TABLE_NAME not in _table_names(db):
        return None
    table = db.open_table(TABLE_NAME)
    if set(table.schema.names) != _FIELDS:  # written by an older version: rebuilt on next scan
        db.drop_table(TABLE_NAME)
        return None
    return table


def drop_table(db) -> None:
    if TABLE_NAME in _table_names(db):
        db.drop_table(TABLE_NAME)


def get_or_create_table(db, dim: int):
    table = open_table(db)
    if table is not None:
        return table
    schema = pa.schema(
        [
            pa.field("path", pa.string()),
            pa.field("root", pa.string()),
            pa.field("loc_kind", pa.string()),
            pa.field("loc_no", pa.int64()),
            pa.field("chunk_no", pa.int64()),
            pa.field("text", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), dim)),
        ]
    )
    return db.create_table(TABLE_NAME, schema=schema)


def _quote(value: str) -> str:
    """SQL string literal for LanceDB filters. Doubling single quotes keeps a file like
    O'Brien.pdf from breaking (or rewriting) the filter."""
    return "'" + value.replace("'", "''") + "'"


def delete_path(table, path: str) -> None:
    table.delete(f"path = {_quote(path)}")


def upsert(table, path: str, rows: list[dict]) -> None:
    delete_path(table, path)
    if rows:
        table.add(rows)


def search(table, query_vector: list[float], limit: int = 8, root: str | None = None) -> list[dict]:
    q = table.search(query_vector).limit(limit)
    if root:
        q = q.where(f"root = {_quote(root)}", prefilter=True)
    return [
        {
            "path": r["path"],
            "loc_kind": r["loc_kind"],
            "loc_no": r["loc_no"],
            "chunk_no": r["chunk_no"],
            "text": r["text"],
            "score": r["_distance"],
        }
        for r in q.to_list()
    ]
