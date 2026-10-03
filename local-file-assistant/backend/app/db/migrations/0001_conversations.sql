CREATE TABLE conversations (
    id         INTEGER PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    pinned     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE messages (
    id         INTEGER PRIMARY KEY,
    conv_id    INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role       TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content    TEXT NOT NULL,
    meta_json  TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX messages_conv ON messages(conv_id, id);
