-- One row per kind of proactive message per local day, so a restart never sends it twice.
-- delivered = 0 records "looked, had nothing to say".
CREATE TABLE nudge_log (
    kind      TEXT NOT NULL,
    day       TEXT NOT NULL,
    delivered INTEGER NOT NULL,
    PRIMARY KEY (kind, day)
);
