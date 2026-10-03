from app.config import settings
from app.core import prefs
from app.db import assistant_db


def test_migrate_is_idempotent_and_creates_tables():
    v = assistant_db.migrate()
    assert v >= 1 and assistant_db.migrate() == v
    conn = assistant_db.connect()
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"conversations", "messages"} <= names


def test_deleting_a_conversation_cascades_to_messages():
    assistant_db.migrate()
    conn = assistant_db.connect()
    cid = conn.execute("INSERT INTO conversations(created_at, updated_at) VALUES (0, 0)").lastrowid
    conn.execute("INSERT INTO messages(conv_id, role, content, created_at) VALUES (?, 'user', 'hi', 0)", (cid,))
    conn.execute("DELETE FROM conversations WHERE id = ?", (cid,))
    assert conn.execute("SELECT COUNT(*) FROM messages WHERE conv_id = ?", (cid,)).fetchone()[0] == 0
    conn.close()


def test_prefs_round_trip_keeps_falsy_values():
    assert prefs.get("nope", 5) == 5
    prefs.set("flag", False)
    assert prefs.get("flag", True) is False
    assert (settings.data_dir / "settings.json").exists()
