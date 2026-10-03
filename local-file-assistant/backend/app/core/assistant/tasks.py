"""Tasks, reminders and calendar events (assistant.db). Times are epoch seconds (UTC); the UI
shows them in the local zone."""
import re
import time
from calendar import monthrange
from datetime import datetime, timedelta

from app.core.assistant import dates
from app.db import assistant_db

_TASK_FIELDS = ("title", "notes", "due_at", "remind_at", "repeat", "status")
_EVENT_FIELDS = ("title", "start_at", "end_at", "all_day", "location", "notes")


def _all(sql: str, args=()) -> list[dict]:
    conn = assistant_db.connect()
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def _exec(sql: str, args=()) -> int:
    conn = assistant_db.connect()
    try:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount
    finally:
        conn.close()


# ---------- tasks ----------

def create(title: str, notes: str = "", due_at=None, remind_at=None, repeat=None, created_from_msg=None) -> dict:
    tid = _exec(
        "INSERT INTO tasks(title, notes, due_at, remind_at, repeat, created_from_msg, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (title.strip(), notes, due_at, remind_at, repeat, created_from_msg, time.time()),
    )
    return get(tid)


def get(tid: int) -> dict | None:
    rows = _all("SELECT * FROM tasks WHERE id = ?", (tid,))
    return rows[0] if rows else None


def list_tasks(status: str | None = None) -> list[dict]:
    where, args = ("WHERE status = ?", (status,)) if status else ("", ())
    return _all(f"SELECT * FROM tasks {where} ORDER BY COALESCE(due_at, remind_at, 9e18), id", args)


def update(tid: int, **fields) -> dict | None:
    fields = {k: v for k, v in fields.items() if k in _TASK_FIELDS}
    if fields:
        if "remind_at" in fields:
            fields["notified_at"] = None  # a new time means it should fire again
        sets = ", ".join(f"{k} = ?" for k in fields)
        _exec(f"UPDATE tasks SET {sets} WHERE id = ?", (*fields.values(), tid))
    return get(tid)


def delete(tid: int) -> None:
    _exec("DELETE FROM tasks WHERE id = ?", (tid,))


def _advance(ts: float, repeat: str) -> float:
    d = datetime.fromtimestamp(ts)
    if repeat == "daily":
        return (d + timedelta(days=1)).timestamp()
    if repeat == "weekly":
        return (d + timedelta(weeks=1)).timestamp()
    year, month = divmod(d.month, 12)
    year, month = d.year + year, month + 1
    return d.replace(year=year, month=month, day=min(d.day, monthrange(year, month)[1])).timestamp()


def complete(tid: int, now: float | None = None) -> dict | None:
    """Done. A repeating task instead moves to its next occurrence and stays open."""
    now = now or time.time()
    t = get(tid)
    if t is None:
        return None
    if not t["repeat"]:
        return update(tid, status="done") and _set_completed(tid, now)
    nxt = {}
    for f in ("due_at", "remind_at"):
        ts = t[f]
        while ts is not None and ts <= now:
            ts = _advance(ts, t["repeat"])
        nxt[f] = ts
    update(tid, **nxt)
    return _set_completed(tid, now)


def _set_completed(tid: int, now: float) -> dict:
    _exec("UPDATE tasks SET completed_at = ? WHERE id = ?", (now, tid))
    return get(tid)


def snooze(tid: int, minutes: int = 10, now: float | None = None) -> dict | None:
    return update(tid, remind_at=(now or time.time()) + minutes * 60)


def due_reminders(now: float) -> list[dict]:
    return _all("SELECT * FROM tasks WHERE status = 'open' AND remind_at IS NOT NULL AND remind_at <= ? AND notified_at IS NULL ORDER BY remind_at", (now,))


def mark_notified(ids: list[int], now: float) -> None:
    for tid in ids:
        _exec("UPDATE tasks SET notified_at = ? WHERE id = ?", (now, tid))


# ---------- events ----------

def create_event(title: str, start_at: float, end_at: float | None = None, all_day: bool = False, location: str = "", notes: str = "", source: str = "manual") -> dict:
    eid = _exec(
        "INSERT INTO events(title, start_at, end_at, all_day, location, notes, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (title.strip(), start_at, end_at or start_at + (86400 if all_day else 3600), int(all_day), location, notes, source),
    )
    return get_event(eid)


def get_event(eid: int) -> dict | None:
    rows = _all("SELECT * FROM events WHERE id = ?", (eid,))
    return {**rows[0], "all_day": bool(rows[0]["all_day"])} if rows else None


def list_events(start: float | None = None, end: float | None = None) -> list[dict]:
    rows = _all("SELECT * FROM events WHERE end_at >= ? AND start_at <= ? ORDER BY start_at", (start or 0, end or 9e18))
    return [{**r, "all_day": bool(r["all_day"])} for r in rows]


def update_event(eid: int, **fields) -> dict | None:
    fields = {k: (int(v) if k == "all_day" else v) for k, v in fields.items() if k in _EVENT_FIELDS}
    if fields:
        _exec(f"UPDATE events SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), eid))
    return get_event(eid)


def delete_event(eid: int) -> None:
    _exec("DELETE FROM events WHERE id = ?", (eid,))


def import_items(parsed: dict) -> dict:
    """Adds items from an .ics file, skipping ones already stored (same title and time)."""
    made = {"tasks": 0, "events": 0}
    have_t = {(t["title"], t["due_at"]) for t in list_tasks()}
    for t in parsed["tasks"]:
        if (t["title"], t["due_at"]) not in have_t:
            row = create(t["title"], t["notes"], t["due_at"], t["remind_at"], t["repeat"])
            if t["status"] == "done":
                update(row["id"], status="done")
            made["tasks"] += 1
    have_e = {(e["title"], e["start_at"]) for e in list_events()}
    for e in parsed["events"]:
        if (e["title"], e["start_at"]) not in have_e:
            create_event(e["title"], e["start_at"], e["end_at"], e["all_day"], e["location"], e["notes"], source="ics")
            made["events"] += 1
    return made


# ---------- from a chat message ----------

_LEAD = re.compile(
    r"^\s*(?:please\s+)?(?:(?:can|could) you\s+)?(?:remind me(?:\s+(?:to|about))?|set (?:a |an )?reminder(?:\s+(?:to|for))?"
    r"|(?:add|create|new)\s+(?:a |an )?(?:task|todo|reminder|event|meeting|appointment)(?:\s+(?:to|for))?\s*:?|todo\s*:?|task\s*:|schedule(?: a| an)?|book(?: a| an)?)\s*",
    re.I,
)
_REPEAT = re.compile(r"\b(?:every\s+(day|week|month)|(daily|weekly|monthly))\b", re.I)
_REPEAT_NAME = {"day": "daily", "week": "weekly", "month": "monthly", "daily": "daily", "weekly": "weekly", "monthly": "monthly"}


def parse_message(message: str, kind: str = "task", now: datetime | None = None) -> dict:
    """A proposal for the confirm card: {kind, title, when (epoch|None), confidence, repeat, all_day}.
    Dates by library, title by rules; nothing is saved here."""
    found = dates.extract(message, now)
    rest = found["rest"]
    repeat = None
    m = _REPEAT.search(rest)
    if m:
        repeat = _REPEAT_NAME[(m.group(1) or m.group(2)).lower()]
        rest = " ".join((rest[: m.start()] + " " + rest[m.end() :]).split())
    title = _LEAD.sub("", rest, count=1).strip(" .,:;-")
    title = re.sub(r"^(?:to|that|about)\s+", "", title, flags=re.I).strip(" .,:;-") or "Untitled"
    when = found["when"]
    all_day = kind == "event" and found["confidence"] == "date_only"
    if when is not None and all_day:
        when = when.replace(hour=0, minute=0)
    return {
        "kind": kind,
        "title": title[:1].upper() + title[1:],
        "when": when.timestamp() if when else None,
        "confidence": found["confidence"],
        "repeat": repeat,
        "all_day": all_day,
    }
