CREATE TABLE tasks (
    id               INTEGER PRIMARY KEY,
    title            TEXT NOT NULL,
    notes            TEXT NOT NULL DEFAULT '',
    due_at           REAL,
    remind_at        REAL,
    repeat           TEXT CHECK (repeat IN ('daily', 'weekly', 'monthly')),
    status           TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
    completed_at     REAL,
    created_from_msg INTEGER,
    created_at       REAL NOT NULL,
    notified_at      REAL  -- set when the reminder for the current remind_at has fired
);
CREATE INDEX tasks_remind ON tasks(status, remind_at);

CREATE TABLE events (
    id       INTEGER PRIMARY KEY,
    title    TEXT NOT NULL,
    start_at REAL NOT NULL,
    end_at   REAL NOT NULL,
    all_day  INTEGER NOT NULL DEFAULT 0,
    location TEXT NOT NULL DEFAULT '',
    notes    TEXT NOT NULL DEFAULT '',
    source   TEXT NOT NULL DEFAULT 'manual'
);
CREATE INDEX events_start ON events(start_at);
