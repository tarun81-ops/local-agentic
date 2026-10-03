"""The morning briefing and other proactive nudges. The facts come from code (build), the words
from a template (text); the model only rephrases them when the user turns that on. Whether a
message may go out now is decided here too: per-kind switches, quiet hours, a daily cap, and
once per day per kind (nudge_log)."""
import re
from datetime import datetime
from datetime import time as dtime

from app.config import settings as app_settings
from app.core import prefs, ram
from app.core.assistant import tasks
from app.core.llm import client, idle
from app.db import assistant_db, sqlite_fts

DEFAULTS = {"briefing": True, "overdue": True, "briefing_time": "08:30", "quiet_start": "22:00", "quiet_end": "07:00", "daily_cap": 3, "use_model": False}
CATCH_UP_S = 4 * 3600  # a briefing for a morning the app was closed is still sent until this long after its time
OVERDUE_HOUR = 17  # the overdue nudge is an evening one
SHOWN = 5  # names listed per section


def settings() -> dict:
    return {**DEFAULTS, **{k: v for k, v in prefs.get("proactive", {}).items() if k in DEFAULTS}}


def _hm(value) -> dtime:
    m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", value) if isinstance(value, str) else None
    if not m:
        raise ValueError("Times look like 08:30.")
    return dtime(int(m[1]), int(m[2]))


def _secs(t: dtime) -> int:
    return t.hour * 3600 + t.minute * 60


def save(new: dict) -> dict:
    """Validates and stores changed settings; raises ValueError with a message for the user."""
    cfg = settings()
    for key, value in new.items():
        if key not in DEFAULTS:
            raise ValueError(f"Unknown setting: {key}")
        if isinstance(DEFAULTS[key], bool):
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be on or off.")
        elif key == "daily_cap":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10:
                raise ValueError("The daily limit is a whole number from 0 to 10.")
        else:
            _hm(value)
        cfg[key] = value
    prefs.set("proactive", cfg)
    return cfg


def in_quiet(t: dtime, start: dtime, end: dtime) -> bool:
    """Quiet hours may wrap midnight (22:00 to 07:00). Equal start and end means no quiet hours."""
    if start == end:
        return False
    return start <= t < end if start < end else t >= start or t < end


# ---------- the facts ----------

def build(now: datetime) -> dict:
    today = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    when = lambda t: t["due_at"] if t["due_at"] is not None else t["remind_at"]  # noqa: E731
    open_tasks = [t for t in tasks.list_tasks("open") if when(t) is not None]
    conn = sqlite_fts.connect(app_settings.db_path)
    try:
        files = sqlite_fts.list_files(conn)
    finally:
        conn.close()
    changed = sorted((f for f in files if f["mtime"] >= now.timestamp() - 86400), key=lambda f: -f["mtime"])
    return {
        "tasks": [{"title": t["title"], "at": when(t)} for t in sorted(open_tasks, key=when) if today <= when(t) < today + 86400],
        "overdue": [t["title"] for t in sorted(open_tasks, key=when) if when(t) < today],
        "events": [{"title": e["title"], "start": e["start_at"], "all_day": e["all_day"]} for e in tasks.list_events(today, today + 86400)],
        "files": {"count": len(changed), "names": [f["path"].replace("\\", "/").rsplit("/", 1)[-1] for f in changed[:SHOWN]]},
    }


def is_empty(data: dict) -> bool:
    return not (data["tasks"] or data["overdue"] or data["events"] or data["files"]["count"])


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%I:%M %p").lstrip("0")


def _list(items: list[str], total: int | None = None) -> str:
    total = total or len(items)
    more = f" and {total - len(items)} more" if total > len(items) else ""
    return ", ".join(items) + more


def text(data: dict) -> str:
    parts = ["Good morning."]
    if data["tasks"]:
        parts.append(f"Due today ({len(data['tasks'])}): " + _list([f"{t['title']} ({_clock(t['at'])})" for t in data["tasks"][:SHOWN]], len(data["tasks"])) + ".")
    if data["events"]:
        parts.append(f"Events ({len(data['events'])}): " + _list([e["title"] if e["all_day"] else f"{e['title']} ({_clock(e['start'])})" for e in data["events"][:SHOWN]], len(data["events"])) + ".")
    if data["overdue"]:
        parts.append(f"Overdue ({len(data['overdue'])}): " + _list(data["overdue"][:SHOWN], len(data["overdue"])) + ".")
    if data["files"]["count"]:
        n = data["files"]["count"]
        parts.append(f"{n} file{'s' if n > 1 else ''} changed in the last day: " + _list(data["files"]["names"], n) + ".")
    return " ".join(parts)


def phrase(data: dict) -> str:
    """The template text, or (if switched on and there is RAM to spare) the model's rewording of it."""
    base = text(data)
    if not settings()["use_model"] or ram.is_low():
        return base
    try:
        idle.touch()
        r = client.get_client().chat.completions.create(
            model=client.current_model(),
            messages=[{"role": "user", "content": "Rewrite this daily briefing as a friendly message of at most 3 short sentences. Use only the facts given and add nothing.\n\n" + base}],
            max_tokens=160,
        )
        return (r.choices[0].message.content or "").strip() or base
    except Exception:
        return base


# ---------- when to send ----------

def _logged(day: str) -> dict[str, int]:
    conn = assistant_db.connect()
    try:
        return {r["kind"]: r["delivered"] for r in conn.execute("SELECT kind, delivered FROM nudge_log WHERE day = ?", (day,))}
    finally:
        conn.close()


def _log(kind: str, day: str, delivered: int) -> None:
    conn = assistant_db.connect()
    try:
        conn.execute("INSERT OR REPLACE INTO nudge_log(kind, day, delivered) VALUES (?, ?, ?)", (kind, day, delivered))
        conn.commit()
    finally:
        conn.close()


def due(now: datetime) -> list[dict]:
    """Messages to send right now: {type, kind, title, body, day}. Nothing is recorded as sent
    until mark_sent, so one that found no listener is offered again on the next check."""
    cfg = settings()
    if in_quiet(now.time(), _hm(cfg["quiet_start"]), _hm(cfg["quiet_end"])):
        return []
    day = now.strftime("%Y-%m-%d")
    logged = _logged(day)
    room = cfg["daily_cap"] - sum(logged.values())
    data = build(now)
    out = []
    if cfg["briefing"] and "briefing" not in logged:
        start = _secs(_hm(cfg["briefing_time"]))
        if start <= _secs(now.time()) <= start + CATCH_UP_S:
            if is_empty(data):
                _log("briefing", day, 0)  # nothing to say today: stay quiet rather than send "nothing"
            elif room > 0:
                out.append({"type": "briefing", "kind": "briefing", "title": "Today's briefing", "body": phrase(data), "day": day})
                room -= 1
    if cfg["overdue"] and "overdue" not in logged and room > 0 and now.hour >= OVERDUE_HOUR and data["overdue"]:
        body = f"{len(data['overdue'])} overdue: " + _list(data["overdue"][:SHOWN], len(data["overdue"]))
        out.append({"type": "nudge", "kind": "overdue", "title": "Overdue tasks", "body": body, "day": day})
    return out


def mark_sent(event: dict) -> None:
    _log(event["kind"], event["day"], 1)
