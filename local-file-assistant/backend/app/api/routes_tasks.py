from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.config import settings
from app.core.assistant import ics, tasks
from app.db import sqlite_fts

router = APIRouter(tags=["tasks"])


class NewTask(BaseModel):
    title: str
    notes: str = ""
    due_at: float | None = None
    remind_at: float | None = None
    repeat: str | None = None
    file_path: str | None = None  # an indexed file this reminder is about


class TaskPatch(BaseModel):
    title: str | None = None
    notes: str | None = None
    due_at: float | None = None
    remind_at: float | None = None
    repeat: str | None = None
    status: str | None = None
    file_path: str | None = None  # "" or null clears it


class NewEvent(BaseModel):
    title: str
    start_at: float
    end_at: float | None = None
    all_day: bool = False
    location: str = ""
    notes: str = ""


class EventPatch(BaseModel):
    title: str | None = None
    start_at: float | None = None
    end_at: float | None = None
    all_day: bool | None = None
    location: str | None = None
    notes: str | None = None


class Parse(BaseModel):
    text: str
    kind: str = "task"


class Snooze(BaseModel):
    minutes: int = 10


class IcsText(BaseModel):
    text: str


def _valid_title(title: str | None) -> None:
    if title is not None and not title.strip():
        raise HTTPException(422, "A title is required.")


def _indexed_file(path: str | None) -> str | None:
    """A reminder may point only at a file in the index (the same rule as /files/open), so a task
    can never become a way to name an arbitrary path. Empty means no file."""
    if not path:
        return None
    conn = sqlite_fts.connect(settings.db_path)
    try:
        if not sqlite_fts.is_indexed(conn, path):
            raise HTTPException(422, "That file isn't in the index (it may have moved).")
    finally:
        conn.close()
    return path


def _need(row):
    if row is None:
        raise HTTPException(404, "Not found")
    return row


@router.get("/tasks")
def list_tasks(status: str | None = None):
    return {"tasks": tasks.list_tasks(status)}


@router.post("/tasks")
def create_task(body: NewTask):
    _valid_title(body.title)
    return tasks.create(body.title, body.notes, body.due_at, body.remind_at, body.repeat, file_path=_indexed_file(body.file_path))


@router.post("/tasks/parse")
def parse_task(body: Parse):
    """Turns a sentence like "call mom tomorrow 5pm" into a proposal; saves nothing."""
    return tasks.parse_message(body.text, "event" if body.kind == "event" else "task")


# fixed paths before /tasks/{id}
@router.get("/tasks/ics", response_class=PlainTextResponse)
def export_ics():
    return PlainTextResponse(ics.export(tasks.list_tasks(), tasks.list_events()), media_type="text/calendar")


@router.post("/tasks/ics")
def import_ics(body: IcsText):
    return tasks.import_items(ics.parse(body.text))


@router.patch("/tasks/{tid}")
def patch_task(tid: int, body: TaskPatch):
    _valid_title(body.title)
    _need(tasks.get(tid))
    fields = body.model_dump(exclude_unset=True)
    if "file_path" in fields:
        fields["file_path"] = _indexed_file(fields["file_path"])
    return tasks.update(tid, **fields)


@router.post("/tasks/{tid}/complete")
def complete_task(tid: int):
    return _need(tasks.complete(tid))


@router.post("/tasks/{tid}/snooze")
def snooze_task(tid: int, body: Snooze):
    return _need(tasks.snooze(tid, body.minutes))


@router.delete("/tasks/{tid}")
def delete_task(tid: int):
    tasks.delete(tid)
    return {"ok": True}


@router.get("/events")
def list_events(start: float | None = None, end: float | None = None):
    return {"events": tasks.list_events(start, end)}


@router.post("/events")
def create_event(body: NewEvent):
    _valid_title(body.title)
    return tasks.create_event(body.title, body.start_at, body.end_at, body.all_day, body.location, body.notes)


@router.patch("/events/{eid}")
def patch_event(eid: int, body: EventPatch):
    _valid_title(body.title)
    _need(tasks.get_event(eid))
    return tasks.update_event(eid, **body.model_dump(exclude_unset=True))


@router.delete("/events/{eid}")
def delete_event(eid: int):
    tasks.delete_event(eid)
    return {"ok": True}
