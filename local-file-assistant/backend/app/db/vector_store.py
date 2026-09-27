from pathlib import Path

import lancedb
import pyarrow as pa

TABLE_NAME = "chunks"


def connect(db_dir: Path):
    db_dir.mkdir(parents=True, exist_ok=True)
    return lancedb.connect(db_dir)


def get_or_create_table(db, dim: int):
    if TABLE_NAME in db.table_names():
        return db.open_table(TABLE_NAME)
    schema = pa.schema(
        [
            pa.field("path", pa.string()),
            pa.field("chunk_no", pa.int64()),
            pa.field("text", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), dim)),
        ]
    )
    return db.create_table(TABLE_NAME, schema=schema)


def upsert(table, path: str, rows: list[dict]) -> None:
    table.delete(f"path = '{path}'")
    if rows:
        table.add(rows)


def search(table, query_vector: list[float], limit: int = 5) -> list[dict]:
    rows = table.search(query_vector).limit(limit).to_list()
    return [{"path": r["path"], "chunk_no": r["chunk_no"], "text": r["text"], "score": r["_distance"]} for r in rows]
