import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.core.assistant import dates, ics, scheduler, tasks
from app.core.assistant.router import task_intent
from app.db import assistant_db
from app.main import app

AUTH = {"Authorization": "Bearer test-token"}
NOW = datetime(2026, 10, 3, 10, 0)  # a Saturday, 10:00 local


@pytest.fixture(autouse=True)
def clean():
    assistant_db.migrate()
    conn = assistant_db.connect()
    conn.execute("DELETE FROM tasks")
    conn.execute("DELETE FROM events")
    conn.commit()
    conn.close()


@pytest.fixture
def client():
    with TestClient(app, base_url="http://127.0.0.1:8756") as c:
        yield c


def _events(body: str):
    return [(m.split("\n", 1)[0].removeprefix("event: "), json.loads(m.split("\n", 1)[1].removeprefix("data: "))) for m in body.strip().split("\n\n")]


@pytest.mark.parametrize(
    "text, when, confidence",
    [
        ("call mom tomorrow at 5pm", datetime(2026, 10, 4, 17, 0), "high"),
        ("send report on Friday 5pm", datetime(2026, 10, 9, 17, 0), "high"),
        ("take a break in 2 hours", datetime(2026, 10, 3, 12, 0), "high"),
        ("pay rent on 15 oct", datetime(2026, 10, 15, 9, 0), "date_only"),  # no time: 09:00 and flagged
        ("submit the report by Friday", datetime(2026, 10, 9, 9, 0), "date_only"),
        ("standup next monday 9am", datetime(2026, 10, 5, 9, 0), "high"),
        ("gym tomorrow morning", datetime(2026, 10, 4, 9, 0), "high"),
        ("dinner tonight", datetime(2026, 10, 3, 20, 0), "high"),
        ("call the bank at 17:30", datetime(2026, 10, 3, 17, 30), "high"),
        ("buy milk", None, "none"),
        ("tell me about summer", None, "none"),
    ],
)
def test_phrases(text, when, confidence):
    r = dates.extract(text, NOW)
    assert (r["when"], r["confidence"]) == (when, confidence)


def test_title_and_repeat_are_taken_out_of_the_sentence():
    p = tasks.parse_message("Remind me to stretch every day at 6pm", "task", NOW)
    assert (p["title"], p["repeat"], p["confidence"]) == ("Stretch", "daily", "high")
    assert tasks.parse_message("add task buy milk", "task", NOW)["title"] == "Buy milk"
    e = tasks.parse_message("schedule a meeting with Sam on 15 oct", "event", NOW)
    assert e["all_day"] and e["title"] == "Meeting with Sam"  # date only: an all-day event


def test_intents():
    assert task_intent("remind me to call mom") == "task" and task_intent("Add a task: buy milk") == "task"
    assert task_intent("schedule a meeting tomorrow 3pm") == "event"
    assert task_intent("what should I remind my team about?") is None and task_intent("hello") is None


def test_repeat_moves_to_the_next_occurrence_and_stays_open():
    t = tasks.create("Water plants", due_at=NOW.timestamp(), remind_at=NOW.timestamp(), repeat="weekly")
    done = tasks.complete(t["id"], now=NOW.timestamp())
    assert done["status"] == "open" and done["due_at"] == datetime(2026, 10, 10, 10, 0).timestamp() and done["notified_at"] is None
    once = tasks.create("Pay tax", due_at=NOW.timestamp())
    assert tasks.complete(once["id"])["status"] == "done"
    month = tasks.create("Rent", due_at=datetime(2026, 1, 31, 9, 0).timestamp(), repeat="monthly")
    assert datetime.fromtimestamp(tasks.complete(month["id"], now=datetime(2026, 1, 31, 10, 0).timestamp())["due_at"]) == datetime(2026, 2, 28, 9, 0)


def test_snooze_and_changing_the_time_rearm_the_reminder():
    t = tasks.create("Call", remind_at=100.0)
    tasks.mark_notified([t["id"]], 200.0)
    assert tasks.due_reminders(300.0) == []
    tasks.snooze(t["id"], 10, now=300.0)
    assert tasks.get(t["id"])["remind_at"] == 900.0 and tasks.get(t["id"])["notified_at"] is None
    assert [x["id"] for x in tasks.due_reminders(901.0)] == [t["id"]]


def test_scheduler_fires_due_reminders_once_and_flags_missed_ones():
    now = NOW.timestamp()
    soon = tasks.create("Due now", remind_at=now - 30)
    tasks.create("Slept through", remind_at=now - 3600)
    tasks.create("Later", remind_at=now + 3600)
    assert scheduler.tick(now) == 0 and tasks.get(soon["id"])["notified_at"] is None  # nobody listening: wait
    q = scheduler.bus.subscribe()
    try:
        assert scheduler.tick(now) == 2 and scheduler.tick(now) == 0
        got = {e["task"]["title"]: e["missed"] for e in (q.get_nowait(), q.get_nowait())}
        assert got == {"Due now": False, "Slept through": True}
    finally:
        scheduler.bus.unsubscribe(q)


def test_done_tasks_do_not_fire():
    t = tasks.create("Already done", remind_at=1.0)
    tasks.update(t["id"], status="done")
    assert tasks.due_reminders(NOW.timestamp()) == []


def test_ics_round_trip_and_reimport_is_idempotent(client):
    tasks.create("Buy milk, eggs; bread", notes="line1\nline2", due_at=1_800_000_000.0, remind_at=1_799_990_000.0, repeat="daily")
    tasks.create_event("Dentist", 1_800_100_000.0, 1_800_103_600.0, location="Main St")
    tasks.create_event("Holiday", datetime(2026, 12, 25).timestamp(), all_day=True)
    text = client.get("/tasks/ics", headers=AUTH).text
    assert text.startswith("BEGIN:VCALENDAR") and "VTODO" in text and "DTSTART;VALUE=DATE:20261225" in text
    parsed = ics.parse(text)
    t = parsed["tasks"][0]
    assert (t["title"], t["notes"], t["due_at"], t["remind_at"], t["repeat"]) == ("Buy milk, eggs; bread", "line1\nline2", 1_800_000_000.0, 1_799_990_000.0, "daily")
    assert {e["title"]: e["all_day"] for e in parsed["events"]} == {"Dentist": False, "Holiday": True}
    assert client.post("/tasks/ics", json={"text": text}, headers=AUTH).json() == {"tasks": 0, "events": 0}  # all already there
    conn = assistant_db.connect()
    conn.execute("DELETE FROM tasks")
    conn.commit()
    conn.close()
    assert client.post("/tasks/ics", json={"text": text}, headers=AUTH).json() == {"tasks": 1, "events": 0}


def test_api_crud(client):
    t = client.post("/tasks", json={"title": "Call Raj", "remind_at": 123.0}, headers=AUTH).json()
    assert client.post("/tasks", json={"title": "  "}, headers=AUTH).status_code == 422
    assert client.patch(f"/tasks/{t['id']}", json={"title": "Call Raj back"}, headers=AUTH).json()["title"] == "Call Raj back"
    assert client.post(f"/tasks/{t['id']}/snooze", json={"minutes": 5}, headers=AUTH).json()["remind_at"] > 123.0
    assert client.post(f"/tasks/{t['id']}/complete", headers=AUTH).json()["status"] == "done"
    assert client.get("/tasks?status=done", headers=AUTH).json()["tasks"][0]["title"] == "Call Raj back"
    assert client.post("/tasks/99999/complete", headers=AUTH).status_code == 404
    e = client.post("/events", json={"title": "Lunch", "start_at": 1000.0}, headers=AUTH).json()
    assert e["end_at"] == 4600.0 and client.get("/events", headers=AUTH).json()["events"][0]["id"] == e["id"]
    client.delete(f"/events/{e['id']}", headers=AUTH)
    client.delete(f"/tasks/{t['id']}", headers=AUTH)
    assert client.get("/tasks", headers=AUTH).json()["tasks"] == []


def test_chat_proposes_a_task_without_saving_it(client):
    ev = _events(client.post("/chat", json={"message": "remind me to call mom tomorrow at 5pm"}, headers=AUTH).text)
    assert [k for k, _ in ev] == ["route", "action", "token", "conversation", "done"]
    p = ev[1][1]["proposal"]
    assert p["kind"] == "task" and p["title"] == "Call mom" and p["when"] and p["confidence"] == "high"
    assert tasks.list_tasks() == []  # only a confirmed card creates it
    assert "Confirm" in ev[2][1]["delta"]
