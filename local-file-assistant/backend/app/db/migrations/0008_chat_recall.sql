-- Retrieval over past conversations: one row per finished exchange (the user's message plus the
-- assistant's answer), keyed by the assistant message. Rows are derived data: they are created
-- from `messages` on demand (core/assistant/chat_recall.py) and vanish with their message or
-- conversation, so deleting a chat also removes it from recall.
CREATE TABLE chat_index (
    msg_id      INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    conv_id     INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    embedding   BLOB,
    embed_model TEXT,
    created_at  REAL NOT NULL
);
CREATE INDEX chat_index_conv ON chat_index(conv_id);

CREATE VIRTUAL TABLE chat_index_fts USING fts5(text, content='chat_index', content_rowid='msg_id');
CREATE TRIGGER chat_index_ai AFTER INSERT ON chat_index BEGIN
    INSERT INTO chat_index_fts(rowid, text) VALUES (new.msg_id, new.text);
END;
CREATE TRIGGER chat_index_ad AFTER DELETE ON chat_index BEGIN
    INSERT INTO chat_index_fts(chat_index_fts, rowid, text) VALUES ('delete', old.msg_id, old.text);
END;
