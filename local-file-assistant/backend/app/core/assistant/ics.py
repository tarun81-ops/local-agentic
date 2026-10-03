"""Minimal iCalendar (.ics) export/import for tasks (VTODO) and events (VEVENT). Hand-rolled:
the app only needs titles, times, notes, location and simple repeats."""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_FREQ = {"daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY"}


def _utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def _unesc(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) in "nN" else m.group(1), s)


def _fold(line: str) -> str:
    return "\r\n ".join(line[i : i + 73] for i in range(0, len(line), 73)) if len(line) > 73 else line


def export(tasks: list[dict], events: list[dict]) -> str:
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Local File Assistant//EN"]
    for e in events:
        out += ["BEGIN:VEVENT", f"UID:lfa-event-{e['id']}@local", f"DTSTAMP:{_utc(e['start_at'])}", f"SUMMARY:{_esc(e['title'])}"]
        if e["all_day"]:
            day = datetime.fromtimestamp(e["start_at"])
            out += [f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}"]
        else:
            out += [f"DTSTART:{_utc(e['start_at'])}", f"DTEND:{_utc(e['end_at'])}"]
        out += [f"LOCATION:{_esc(e['location'])}"] if e["location"] else []
        out += [f"DESCRIPTION:{_esc(e['notes'])}"] if e["notes"] else []
        out.append("END:VEVENT")
    for t in tasks:
        out += ["BEGIN:VTODO", f"UID:lfa-task-{t['id']}@local", f"DTSTAMP:{_utc(t['created_at'])}", f"SUMMARY:{_esc(t['title'])}"]
        out += [f"DUE:{_utc(t['due_at'])}"] if t["due_at"] else []
        out += [f"X-LFA-REMIND:{_utc(t['remind_at'])}"] if t["remind_at"] else []
        out += [f"RRULE:FREQ={_FREQ[t['repeat']]}"] if t["repeat"] else []
        out += [f"DESCRIPTION:{_esc(t['notes'])}"] if t["notes"] else []
        out += ["STATUS:COMPLETED" if t["status"] == "done" else "STATUS:NEEDS-ACTION", "END:VTODO"]
    out.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in out) + "\r\n"


def _when(params: str, value: str) -> tuple[float, bool] | None:
    """(epoch, is_all_day) for a DTSTART/DUE value; floating and TZID times are read in that zone."""
    try:
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp(), False
        if len(value) == 8:
            return datetime.strptime(value, "%Y%m%d").timestamp(), True
        dt = datetime.strptime(value, "%Y%m%dT%H%M%S")
        tz = re.search(r"TZID=([^;:]+)", params)
        if tz:
            try:
                dt = dt.replace(tzinfo=ZoneInfo(tz.group(1)))
            except Exception:  # unknown zone name: read it as local time
                pass
        return dt.timestamp(), False
    except ValueError:
        return None


def parse(text: str) -> dict:
    """{"tasks": [...], "events": [...]} with the same fields the API uses; unreadable items are skipped."""
    lines = re.sub(r"\r?\n[ \t]", "", text).splitlines()
    tasks: list[dict] = []
    events: list[dict] = []
    block: dict | None = None
    kind = ""
    for line in lines:
        if line in ("BEGIN:VEVENT", "BEGIN:VTODO"):
            kind, block = line[6:], {}
        elif line in ("END:VEVENT", "END:VTODO") and block is not None:
            title = block.get("SUMMARY", "").strip()
            if kind == "VEVENT" and title and "DTSTART" in block:
                start, all_day = block["DTSTART"]
                end = block.get("DTEND", (start + (86400 if all_day else 3600), False))[0]
                events.append({"title": title, "start_at": start, "end_at": end, "all_day": all_day, "location": block.get("LOCATION", ""), "notes": block.get("DESCRIPTION", "")})
            elif kind == "VTODO" and title:
                rule = re.search(r"FREQ=(DAILY|WEEKLY|MONTHLY)", block.get("RRULE", ""))
                tasks.append({
                    "title": title, "notes": block.get("DESCRIPTION", ""),
                    "due_at": block["DUE"][0] if "DUE" in block else None,
                    "remind_at": block["X-LFA-REMIND"][0] if "X-LFA-REMIND" in block else None,
                    "repeat": rule.group(1).lower() if rule else None,
                    "status": "done" if block.get("STATUS") == "COMPLETED" else "open",
                })
            block = None
        elif block is not None and ":" in line:
            head, value = line.split(":", 1)
            name = head.split(";")[0].upper()
            if name in ("DTSTART", "DTEND", "DUE", "X-LFA-REMIND"):
                parsed = _when(head, value)
                if parsed:
                    block[name] = parsed
            elif name in ("SUMMARY", "LOCATION", "DESCRIPTION"):
                block[name] = _unesc(value)
            elif name in ("RRULE", "STATUS"):
                block[name] = value
    return {"tasks": tasks, "events": events}
