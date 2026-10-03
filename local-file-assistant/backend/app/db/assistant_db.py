"""assistant.db: conversations, memories, tasks. Separate from index.db because that one is
disposable (dropped on a schema bump); this one is migrated forward and never dropped.
Migrations are db/migrations/NNNN_*.sql, applied in order and tracked in PRAGMA user_version."""
import sqlite3
from pathlib import Path

from app.config import settings

MIGRATIONS = Path(__file__).parent / "migrations"


def connect() -> sqlite3.Connection:
    settings.assistant_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.assistant_db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate() -> int:
    """Apply pending migrations; returns the schema version afterwards."""
    conn = connect()
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for sql in sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            n = int(sql.name[:4])
            if n > version:
                # executescript commits first, so the version bump is part of the script.
                conn.executescript(f"BEGIN;\n{sql.read_text(encoding='utf-8')}\nPRAGMA user_version={n};\nCOMMIT;")
                version = n
        return version
    finally:
        conn.close()
