from datetime import datetime, timedelta
from datetime import time as dtime

import pytest
from fastapi.testclient import TestClient

from app.core import prefs
from app.core.assistant import briefing, scheduler, tasks
from app.db import assistant_db
from app.main import app

AUTH = {"Authorization": "Bearer test-token"}
SAT = datetime(2026, 10, 3)  # a Saturday


def at(h, m=0, day=SAT):
    return day.replace(hour=h, minute=m)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    assistant_db.migrate()
    conn = assistant_db.connect()
    for table in ("tasks", "events", "nudge_log"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()
    prefs.set("proactive", {})
    monkeypatch.setattr(briefing.sqlite_fts, "list_files", lambda conn, root=None, limit=5000: [])
    yield
    prefs.set("proactive", {})


def seed():
    tasks.create("Call mom", due_at=at(17).timestamp())
    tasks.create("Pay rent", due_at=(SAT - timedelta(days=2)).timestamp())
    tasks.create_event("Dentist", at(15).timestamp(), at(16).timestamp())


def test_quiet_hours_wrap_midnight():
    wrap = (dtime(22), dtime(7))
    assert [briefing.in_quiet(dtime(h, m), *wrap) for h, m in [(23, 0), (6, 59), (7, 0), (12, 0), (22, 0)]] == [True, True, False, False, True]
    assert briefing.in_quiet(dtime(13, 30), dtime(13), dtime(14)) and not briefing.in_quiet(dtime(14), dtime(13), dtime(14))
    assert not briefing.in_quiet(dtime(3), dtime(8), dtime(8))  # equal: no quiet hours


def test_settings_are_validated_and_saved():
    assert briefing.settings()["briefing_time"] == "08:30"
    assert briefing.save({"briefing_time": "07:15", "daily_cap": 2, "use_model": True})["daily_cap"] == 2
    assert briefing.settings()["briefing_time"] == "07:15"
    for bad in ({"briefing_time": "25:00"}, {"quiet_start": "7pm"}, {"daily_cap": 11}, {"daily_cap": True}, {"overdue": "yes"}, {"nope": 1}):
        with pytest.raises(ValueError):
            briefing.save(bad)
    assert briefing.settings()["briefing_time"] == "07:15"  # a refused change changes nothing


def test_the_briefing_text_lists_the_days_facts(monkeypatch):
    seed()
    monkeypatch.setattr(briefing.sqlite_fts, "list_files", lambda conn, root=None, limit=5000: [{"path": "C:\\d\\a.pdf", "mtime": at(8).timestamp()}, {"path": "C:\\d\\old.pdf", "mtime": 1.0}])
    t = briefing.text(briefing.build(at(8, 30)))
    assert "Due today (1): Call mom (5:00 PM)" in t and "Dentist (3:00 PM)" in t and "Overdue (1): Pay rent" in t and "1 file changed in the last day: a.pdf" in t


def test_briefing_is_sent_once_a_day_at_its_time():
    seed()
    assert briefing.due(at(8, 29)) == []
    [ev] = briefing.due(at(8, 30))
    assert ev["type"] == "briefing" and "Call mom" in ev["body"]
    assert briefing.due(at(8, 31))[0]["kind"] == "briefing"  # not recorded until mark_sent
    briefing.mark_sent(ev)
    assert briefing.due(at(9)) == [] and briefing.due(at(10)) == []
    assert briefing.due(at(8, 30, SAT + timedelta(days=1)))  # a new day, a new briefing (Sunday's tasks aside, overdue remains)


def test_a_late_start_still_gets_the_briefing_but_not_hours_later():
    seed()
    assert briefing.due(at(12, 0)) and not briefing.due(at(12, 31))
    seed()


def test_nothing_is_sent_in_quiet_hours_and_waits_until_they_end():
    seed()
    briefing.save({"briefing_time": "06:00"})
    assert briefing.due(at(6, 30)) == []  # quiet until 07:00
    assert briefing.due(at(7, 5))[0]["kind"] == "briefing"


def test_empty_days_send_nothing_and_stay_quiet_all_day():
    assert briefing.due(at(8, 30)) == []
    tasks.create("Added later", due_at=at(17).timestamp())
    assert briefing.due(at(9)) == []  # today's briefing was already skipped


def test_daily_cap_and_per_kind_switches():
    seed()
    briefing.save({"daily_cap": 1})
    [ev] = briefing.due(at(18))  # past briefing time + catch-up; only overdue is eligible... briefing window closed
    assert ev["kind"] == "overdue" and "Pay rent" in ev["body"]
    briefing.mark_sent(ev)
    briefing.save({"daily_cap": 2, "overdue": False})
    assert briefing.due(at(18)) == []
    briefing.save({"daily_cap": 0, "overdue": True})
    assert briefing.due(at(8, 30)) == []
    briefing.save({"daily_cap": 3, "briefing": False})
    assert briefing.due(at(8, 30)) == []


def test_cap_counts_what_was_actually_sent():
    seed()
    briefing.save({"daily_cap": 1})
    [ev] = briefing.due(at(8, 30))
    briefing.mark_sent(ev)
    assert briefing.due(at(18)) == []  # the cap of 1 is used up
    briefing.save({"daily_cap": 2})
    assert [e["kind"] for e in briefing.due(at(18))] == ["overdue"]


def test_overdue_nudge_is_an_evening_one_and_only_when_something_is_overdue():
    seed()
    briefing.save({"briefing": False})
    assert briefing.due(at(16, 59)) == [] and [e["kind"] for e in briefing.due(at(17))] == ["overdue"]
    conn = assistant_db.connect()
    conn.execute("DELETE FROM tasks")
    conn.commit()
    conn.close()
    assert briefing.due(at(18)) == []


def test_scheduler_waits_for_a_listener_before_recording_a_send():
    seed()
    now = at(8, 30)
    assert scheduler.nudge_tick(now) == 0 and briefing.due(now)  # nobody listening: still due
    q = scheduler.bus.subscribe()
    try:
        assert scheduler.nudge_tick(now) == 1 and scheduler.nudge_tick(now) == 0
        assert q.get_nowait()["type"] == "briefing"
    finally:
        scheduler.bus.unsubscribe(q)


def test_api():
    seed()
    with TestClient(app, base_url="http://127.0.0.1:8756") as c:
        assert c.get("/proactive/settings", headers=AUTH).json()["daily_cap"] == 3
        assert c.put("/proactive/settings", json={"briefing_time": "07:45"}, headers=AUTH).json()["briefing_time"] == "07:45"
        assert c.put("/proactive/settings", json={"briefing_time": "later"}, headers=AUTH).status_code == 422
        p = c.get("/proactive/preview", headers=AUTH).json()
    assert p["empty"] is False and p["text"].startswith("Good morning.")
