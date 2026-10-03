-- Which file the user opened after which search (a small ranking prior), and which answers
-- they marked helpful (1) or not (-1). Deleting a conversation deletes its ratings.
CREATE TABLE file_opens (
    id        INTEGER PRIMARY KEY,
    path      TEXT NOT NULL,
    query     TEXT NOT NULL DEFAULT '',
    opened_at REAL NOT NULL
);
CREATE INDEX file_opens_path ON file_opens(path);

CREATE TABLE feedback (
    msg_id     INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    rating     INTEGER NOT NULL CHECK (rating IN (-1, 1)),
    created_at REAL NOT NULL
);
