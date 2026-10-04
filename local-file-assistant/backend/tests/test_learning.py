import json

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.assistant import conversation, learning
from app.core.search import hybrid
from app.db import assistant_db
from app.main import app

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture(autouse=True)
def clean():
    assistant_db.migrate()
    conn = assistant_db.connect()
    conn.execute("DELETE FROM file_opens")
    conn.execute("DELETE FROM conversations")
    conn.commit()
    conn.close()


@pytest.fixture
def client():
    with TestClient(app, base_url="http://127.0.0.1:8756") as c:
        yield c


def opens(path, query, n):
    for _ in range(n):
        learning.record_open(path, query)


def test_one_stray_open_changes_nothing_and_the_boost_is_capped():
    opens("a.pdf", "lease agreement", 1)
    assert learning.open_prior("lease agreement", ["a.pdf"]) == {"a.pdf": 0.5}  # half a position: not enough to pass a neighbour
    opens("a.pdf", "lease agreement", 50)
    assert learning.open_prior("lease agreement", ["a.pdf"]) == {"a.pdf": float(settings.open_prior_max_shift)}


def test_only_similar_searches_count_and_only_for_the_files_in_play():
    opens("a.pdf", "rent receipt march", 4)
    assert learning.open_prior("rent receipt", ["a.pdf"]) == {"a.pdf": pytest.approx(1.33, abs=0.01)}  # 2 of 3 words shared
    assert learning.open_prior("tax return", ["a.pdf"]) == {}  # a different search
    assert learning.open_prior("rent receipt march", ["b.pdf"]) == {}  # a different file
    assert learning.open_prior("", ["a.pdf"]) == {}


def test_prior_can_be_switched_off(monkeypatch):
    opens("a.pdf", "lease", 6)
    monkeypatch.setattr(settings, "open_prior_max_shift", 0)
    assert learning.open_prior("lease", ["a.pdf"]) == {}


def rows(*names):
    return [{"path": n, "text": n} for n in names]


def test_ranking_moves_a_habitually_opened_file_up_by_at_most_the_cap():
    order = lambda r: [x["path"] for x in r]  # noqa: E731
    base = rows("1.pdf", "2.pdf", "3.pdf", "4.pdf", "5.pdf")
    assert order(hybrid._apply_open_prior("lease", base)) == order(base)  # nothing learned yet
    opens("5.pdf", "lease", 10)  # opened for this search again and again: capped at 2 places
    assert order(hybrid._apply_open_prior("lease", base)) == ["1.pdf", "2.pdf", "5.pdf", "3.pdf", "4.pdf"]
    opens("1.pdf", "lease", 10)  # the top result stays on top
    assert order(hybrid._apply_open_prior("lease", base))[0] == "1.pdf"


def test_a_failing_prior_never_breaks_search(monkeypatch):
    monkeypatch.setattr(hybrid.learning, "open_prior", lambda *a: (_ for _ in ()).throw(RuntimeError("db locked")))
    base = rows("1.pdf", "2.pdf")
    assert hybrid._apply_open_prior("x", base) == base


def _exchange(question="what is the total?", answer="It is 5 (a.pdf, page 1).", cites=None):
    conv = conversation.create("t")
    conversation.add_messages(conv["id"], [("user", question, {}), ("assistant", answer, {"citations": cites or []})])
    msgs = conversation.get(conv["id"])["messages"]
    return conv["id"], msgs[1]["id"]


def test_feedback_is_stored_toggled_and_shown_when_a_chat_is_reopened(client):
    cid, mid = _exchange()
    assert client.post("/learning/feedback", json={"msg_id": mid, "rating": -1}, headers=AUTH).status_code == 200
    assert conversation.get(cid)["messages"][1]["rating"] == -1
    client.post("/learning/feedback", json={"msg_id": mid, "rating": 1}, headers=AUTH)
    assert learning.stats() == {"opens": 0, "helpful": 1, "not_helpful": 0}
    client.post("/learning/feedback", json={"msg_id": mid, "rating": 0}, headers=AUTH)
    assert conversation.get(cid)["messages"][1]["rating"] is None
    assert client.post("/learning/feedback", json={"msg_id": mid, "rating": 5}, headers=AUTH).status_code == 422
    user_msg = conversation.get(cid)["messages"][0]["id"]
    assert client.post("/learning/feedback", json={"msg_id": user_msg, "rating": 1}, headers=AUTH).status_code == 404  # only answers


def test_not_helpful_questions_export_as_eval_cases(client):
    _, bad = _exchange("what is the lease end date?", "It ends in 2030.", [{"file": "lease.pdf"}, {"file": "lease.pdf"}, {"file": "notes.docx"}])
    _, good = _exchange("hello", "hi")
    client.post("/learning/feedback", json={"msg_id": bad, "rating": -1}, headers=AUTH)
    client.post("/learning/feedback", json={"msg_id": good, "rating": 1}, headers=AUTH)
    lines = [json.loads(x) for x in client.get("/learning/export", headers=AUTH).text.splitlines()]
    assert len(lines) == 1 and lines[0]["q"] == "what is the lease end date?" and lines[0]["files"] == ["lease.pdf", "notes.docx"]
    assert lines[0]["answer"] == "It ends in 2030."


def test_deleting_a_conversation_deletes_its_ratings_and_wipe_clears_everything(client):
    cid, mid = _exchange()
    client.post("/learning/feedback", json={"msg_id": mid, "rating": 1}, headers=AUTH)
    conversation.delete(cid)
    assert learning.stats()["helpful"] == 0
    _, mid2 = _exchange()
    client.post("/learning/feedback", json={"msg_id": mid2, "rating": -1}, headers=AUTH)
    opens("a.pdf", "x", 3)
    assert client.post("/learning/wipe", headers=AUTH).json() == {"opens": 3, "ratings": 1}
    assert learning.stats() == {"opens": 0, "helpful": 0, "not_helpful": 0}


def test_opening_a_file_records_the_search_that_found_it(client, monkeypatch, tmp_path):
    from app.api import routes_files

    f = tmp_path / "lease.pdf"
    f.write_text("x")
    monkeypatch.setattr(routes_files.sqlite_fts, "is_indexed", lambda conn, p: True)
    monkeypatch.setattr(routes_files.os, "startfile", lambda p: None, raising=False)
    monkeypatch.setattr(routes_files.subprocess, "Popen", lambda *a, **k: None)
    assert client.post("/files/open", json={"path": str(f), "query": "my lease"}, headers=AUTH).status_code == 200
    assert client.post("/files/open", json={"path": str(f)}, headers=AUTH).status_code == 200  # old callers send no query
    assert learning.stats()["opens"] == 2
    monkeypatch.setattr(routes_files.learning, "record_open", lambda *a: (_ for _ in ()).throw(RuntimeError("locked")))
    assert client.post("/files/open", json={"path": str(f)}, headers=AUTH).status_code == 200  # learning never blocks opening
