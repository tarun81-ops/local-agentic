"""Reminders tied to documents, and the opt-in new-file nudge."""
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core import indexer, personalize, prefs
from app.core.assistant import briefing, scheduler, tasks
from app.db import assistant_db
from app.main import app

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture(autouse=True)
def clean():
    assistant_db.migrate()
    conn = assistant_db.connect()
    for table in ("tasks", "events", "nudge_log"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()
    personalize.update(personalize.DEFAULTS)
    prefs.set("inbox_checked", {})
    prefs.set("proactive", {"briefing": False, "overdue": False, "quiet_start": "00:00", "quiet_end": "00:00", "daily_cap": 3})
    yield
    personalize.update(personalize.DEFAULTS)
    prefs.set("inbox_checked", {})
    prefs.set("proactive", {})


@pytest.fixture
def client():
    with TestClient(app, base_url="http://127.0.0.1:8756") as c:
        yield c


@pytest.fixture
def indexed(tmp_path):
    root = (tmp_path / "docs").resolve()
    root.mkdir()
    f = root / "syllabus.txt"
    f.write_text("unit one and unit two", encoding="utf-8")
    indexer.index_folder(root, settings.db_path, settings.vector_db_dir)
    yield f
    indexer.remove_root(str(root), settings.db_path, settings.vector_db_dir)


# ---------- reminders tied to a file ----------

def test_task_can_point_at_an_indexed_file_and_clear_it(client, indexed):
    t = client.post("/tasks", json={"title": "Read it", "file_path": str(indexed)}, headers=AUTH).json()
    assert t["file_path"] == str(indexed)
    assert client.get("/tasks", headers=AUTH).json()["tasks"][0]["file_path"] == str(indexed)
    cleared = client.patch(f"/tasks/{t['id']}", json={"file_path": ""}, headers=AUTH).json()
    assert cleared["file_path"] is None
    assert client.patch(f"/tasks/{t['id']}", json={"file_path": str(indexed)}, headers=AUTH).json()["file_path"] == str(indexed)
    assert client.patch(f"/tasks/{t['id']}", json={"title": "Renamed"}, headers=AUTH).json()["file_path"] == str(indexed)  # untouched by other edits


def test_task_rejects_files_that_are_not_in_the_index(client, indexed, tmp_path):
    stray = tmp_path / "stray.txt"
    stray.write_text("x", encoding="utf-8")
    for bad in (str(stray), r"C:\Windows\notepad.exe", str(indexed) + ".nope"):
        r = client.post("/tasks", json={"title": "Sneaky", "file_path": bad}, headers=AUTH)
        assert r.status_code == 422 and "index" in r.json()["detail"]
    t = client.post("/tasks", json={"title": "Fine"}, headers=AUTH).json()
    assert t["file_path"] is None
    assert client.patch(f"/tasks/{t['id']}", json={"file_path": str(stray)}, headers=AUTH).status_code == 422


def test_reminder_event_carries_the_file(indexed):
    tasks.create("Review the syllabus", remind_at=time.time() - 5, file_path=str(indexed))
    q = scheduler.bus.subscribe()
    try:
        assert scheduler.tick() == 1
        event = q.get(timeout=1)
    finally:
        scheduler.bus.unsubscribe(q)
    assert event["type"] == "reminder" and event["task"]["file_path"] == str(indexed)


# ---------- new-file nudge ----------

@pytest.fixture
def inbox(tmp_path):
    folder = tmp_path / "Downloads"
    folder.mkdir()
    personalize.update({"inbox_folders": [str(folder)]})
    return folder


def noon():
    """Now (the name is historical): file birth times are real, so the clock must be too."""
    return datetime.now()


def test_inbox_folders_are_validated(tmp_path):
    for bad in ([str(tmp_path / "missing")], "Downloads", [5], [str(tmp_path)] * 11):
        with pytest.raises(ValueError):
            personalize.update({"inbox_folders": bad})
    one = personalize.update({"inbox_folders": [str(tmp_path), str(tmp_path).upper()]})["inbox_folders"]
    assert len(one) == 1  # the same folder twice is one


def test_off_by_default_and_first_look_only_sets_a_starting_point(inbox):
    personalize.update({"inbox_folders": []})
    (inbox / "a.pdf").write_text("x", encoding="utf-8")
    assert briefing.due(noon()) == []
    personalize.update({"inbox_folders": [str(inbox)]})
    assert briefing.due(noon()) == []  # files already there are not announced
    assert str(inbox) in prefs.get("inbox_checked")


def test_new_files_after_the_starting_point_are_offered_once(inbox):
    briefing.due(noon())  # starting point
    time.sleep(0.05)
    (inbox / "notes.pdf").write_text("x", encoding="utf-8")
    (inbox / "photo.png").write_text("x", encoding="utf-8")
    (inbox / "big.zip.crdownload").write_text("x", encoding="utf-8")  # still downloading
    (inbox / ".hidden").write_text("x", encoding="utf-8")
    (inbox / "~$lock.docx").write_text("x", encoding="utf-8")
    [event] = briefing.due(noon())
    assert event["kind"] == "new_files" and event["page"] == "organize" and event["type"] == "nudge"
    assert "2 new files in Downloads (2)" in event["body"] and "notes.pdf" in event["body"] and "crdownload" not in event["body"]
    assert "Nothing moves until you approve" in event["body"]
    assert len(briefing.due(noon())) == 1  # not marked sent: offered again
    briefing.mark_sent(event)
    assert briefing.due(noon()) == []  # once per day per kind
    conn = assistant_db.connect()
    conn.execute("DELETE FROM nudge_log")  # pretend it is a new day
    conn.commit()
    conn.close()
    assert briefing.due(noon()) == []  # the same two files are not announced again
    time.sleep(0.05)
    (inbox / "later.docx").write_text("x", encoding="utf-8")
    [again] = briefing.due(noon())
    assert "1 new file in" in again["body"] and "later.docx" in again["body"]


def test_nothing_is_moved_or_changed_in_the_folder(inbox):
    briefing.due(noon())
    time.sleep(0.05)
    (inbox / "a.pdf").write_text("x", encoding="utf-8")
    briefing.due(noon())
    assert sorted(p.name for p in inbox.iterdir()) == ["a.pdf"]


def test_quiet_hours_cap_and_log_are_respected(inbox):
    prefs.set("inbox_checked", {str(inbox): 0.0})  # every file counts as new
    (inbox / "a.pdf").write_text("x", encoding="utf-8")
    prefs.set("proactive", {"briefing": False, "overdue": False, "quiet_start": "22:00", "quiet_end": "07:00", "daily_cap": 3})
    late = datetime.now().replace(hour=23, minute=0, second=0, microsecond=0)
    assert briefing.due(late) == []  # quiet hours
    prefs.set("proactive", {"briefing": False, "overdue": False, "quiet_start": "00:00", "quiet_end": "00:00", "daily_cap": 1})
    day = noon().strftime("%Y-%m-%d")
    briefing._log("overdue", day, 1)  # today's one allowed message is already used
    assert briefing.due(noon()) == []
    prefs.set("proactive", {"briefing": False, "overdue": False, "quiet_start": "00:00", "quiet_end": "00:00", "daily_cap": 2})
    assert [e["kind"] for e in briefing.due(noon())] == ["new_files"]
