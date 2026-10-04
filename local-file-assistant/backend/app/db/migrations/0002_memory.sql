CREATE TABLE memories (
    id           INTEGER PRIMARY KEY,
    text         TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'fact',
    source_conv  INTEGER,
    source_msg   INTEGER,
    created_at   REAL NOT NULL,
    last_used_at REAL,
    use_count    INTEGER NOT NULL DEFAULT 0,
    pinned       INTEGER NOT NULL DEFAULT 0,
    embedding    BLOB,
    embed_model  TEXT
);

CREATE VIRTUAL TABLE memories_fts USING fts5(text, content='memories', content_rowid='id');
CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER memories_au AFTER UPDATE OF text ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
END;

-- Messages up to this id have been looked at for memorable facts.
ALTER TABLE conversations ADD COLUMN extracted_msg_id INTEGER NOT NULL DEFAULT 0;
