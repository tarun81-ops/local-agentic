CREATE TABLE tool_runs (
    id          INTEGER PRIMARY KEY,
    conv_id     INTEGER,
    tool        TEXT NOT NULL,
    args_json   TEXT NOT NULL,
    risk        TEXT NOT NULL CHECK (risk IN ('read', 'reversible')),
    status      TEXT NOT NULL DEFAULT 'proposed'
                CHECK (status IN ('proposed', 'running', 'done', 'failed', 'rejected', 'undoing', 'undone')),
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL
);
CREATE INDEX tool_runs_conv ON tool_runs(conv_id, id);
