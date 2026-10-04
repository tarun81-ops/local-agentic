-- Facts learned from chats wait for the user's review before they are used.
ALTER TABLE memories ADD COLUMN status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'pending'));

-- Saved searches (the user's own data; the learning wipe leaves them alone).
CREATE TABLE collections (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    query      TEXT NOT NULL,
    root       TEXT,
    pinned     INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

-- A reminder can point at an indexed file.
ALTER TABLE tasks ADD COLUMN file_path TEXT;
