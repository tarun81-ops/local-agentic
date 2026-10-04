import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.assistant import conversation, memory_extract, memory_store
from app.core.assistant.router import memory_command
from app.db import assistant_db
from app.main import app

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture(autouse=True)
def clean():
    assistant_db.migrate()
    memory_store.wipe()
    conn = assistant_db.connect()  # other test files leave idle chats that extraction would also pick up
    conn.execute("DELETE FROM conversations")
    conn.commit()
    conn.close()
    yield
    memory_store.wipe()


@pytest.fixture
def client():
    with TestClient(app, base_url="http://127.0.0.1:8756") as c:
        yield c


def _events(body: str):
    return [(m.split("\n", 1)[0].removeprefix("event: "), json.loads(m.split("\n", 1)[1].removeprefix("data: "))) for m in body.strip().split("\n\n")]


def test_add_dedupes_and_recall_ranks_the_relevant_fact_first():
    assert memory_store.add("My sister Priya lives in Pune")
    assert memory_store.add("I prefer dark mode in every editor")
    assert memory_store.add("my sister priya lives in pune") is None  # same fact
    top = memory_store.recall("where does my sister live", k=2)
    assert top[0]["text"].startswith("My sister Priya")
    assert memory_store.recall("quantum chromodynamics") == []


def test_delete_and_wipe_remove_facts_from_recall_and_the_search_index():
    a = memory_store.add("I drink green tea every morning")
    memory_store.delete(a["id"])
    assert memory_store.recall("green tea") == []
    memory_store.add("I run on weekends")
    assert memory_store.wipe() == 1 and memory_store.list_all() == []


def test_changing_the_embedding_model_re_embeds(monkeypatch):
    memory_store.add("I use Linux at work")
    monkeypatch.setattr(settings, "embedding_model", "another-model")
    assert memory_store.recall("Linux")  # recall refreshes vectors first
    conn = assistant_db.connect()
    assert conn.execute("SELECT embed_model FROM memories").fetchone()[0] == "another-model"
    conn.close()


def test_edit_updates_text_and_keyword_index():
    m = memory_store.add("I live in Delhi")
    assert memory_store.update(m["id"], text="I live in Mumbai", pinned=True)
    assert memory_store.recall("Mumbai")[0]["pinned"] and memory_store.recall("Delhi") == []


def test_extraction_filters_questions_and_blocked_categories():
    kept = memory_extract.clean([
        "The user prefers short answers",
        "What is the capital of France?",
        "My card number is 4111 1111 1111 1111",
        "I take medication every night",
        "The user's password is hunter2",
        "hi",
        42,
    ])
    assert kept == ["The user prefers short answers"]


def test_extraction_runs_after_idle_once_and_skips_when_ram_is_low(monkeypatch):
    conv = conversation.create("t")
    conversation.add_messages(conv["id"], [("user", "I am learning Rust for my side project", {}), ("assistant", "Nice!", {})], at=time.time() - 10_000)
    calls = []
    monkeypatch.setattr(memory_extract.client, "llm_json", lambda p, max_tokens=300: calls.append(p) or {"facts": ["The user is learning Rust", "Is this a question?"]})
    monkeypatch.setattr(memory_extract.ram, "is_low", lambda: True)
    assert memory_extract.run_once() == 0 and not calls
    monkeypatch.setattr(memory_extract.ram, "is_low", lambda: False)
    assert memory_extract.run_once() == 1
    assert memory_extract.run_once() == 0 and len(calls) == 1  # messages are only read once
    assert "Rust" in memory_store.list_all()[0]["text"]


def test_recent_conversations_are_left_alone(monkeypatch):
    conv = conversation.create("t")
    conversation.add_messages(conv["id"], [("user", "I like tea a lot", {}), ("assistant", "ok", {})])
    monkeypatch.setattr(memory_extract.client, "llm_json", lambda *a, **k: pytest.fail("chat still active"))
    assert memory_extract.run_once() == 0


def test_remember_and_forget_commands_through_chat(client):
    assert memory_command("remember that my cat is called Mia") == ("remember", "my cat is called Mia")
    ev = _events(client.post("/chat", json={"message": "Remember that my cat is called Mia."}, headers=AUTH).text)
    assert ev[0] == ("route", {"route": "memory"}) and "Remembered" in ev[1][1]["delta"]
    assert memory_store.list_all()[0]["text"] == "my cat is called Mia"
    ev = _events(client.post("/chat", json={"message": "forget my cat"}, headers=AUTH).text)
    assert "Forgot" in ev[1][1]["delta"] and memory_store.list_all() == []


def test_recalled_facts_reach_the_prompt_and_are_reported(client, monkeypatch):
    from app.api import routes_chat

    memory_store.add("The user's sister is called Priya")
    seen = []

    def fake_chat(message, history=None, model=None, style=""):
        seen.append(message)
        yield "ok"
        return False

    monkeypatch.setattr(routes_chat, "chat_stream", fake_chat)
    ev = _events(client.post("/chat", json={"message": "what is my sister called", "mode": "chat"}, headers=AUTH).text)
    assert "Priya" in seen[0] and "never cite them as files" in seen[0]
    used = next(d for k, d in ev if k == "memory_used")["memories"]
    assert used[0]["text"].endswith("Priya")
    cid = next(d for k, d in ev if k == "conversation")["id"]
    assert client.get(f"/conversations/{cid}", headers=AUTH).json()["messages"][1]["meta"]["memory_used"] == used


def test_memory_api(client):
    made = client.post("/memory", json={"text": "I like chess"}, headers=AUTH).json()
    assert client.post("/memory", json={"text": "I like chess"}, headers=AUTH).status_code == 409
    assert client.patch(f"/memory/{made['id']}", json={"pinned": True}, headers=AUTH).status_code == 200
    assert client.patch("/memory/99999", json={"pinned": True}, headers=AUTH).status_code == 404
    assert client.get("/memory/export", headers=AUTH).json()["memories"][0]["pinned"]
    assert client.post("/memory/wipe", headers=AUTH).json() == {"deleted": 1}


# ---------- review step: learned facts wait as pending ----------

@pytest.fixture
def review(monkeypatch):
    from app.core import personalize

    personalize.update({"memory_review": True})
    yield
    personalize.update(personalize.DEFAULTS)


def _extract(monkeypatch, facts):
    conv = conversation.create("t")
    conversation.add_messages(conv["id"], [("user", "I am learning Rust for my side project", {}), ("assistant", "Nice!", {})], at=time.time() - 10_000)
    monkeypatch.setattr(memory_extract.client, "llm_json", lambda p, max_tokens=300: {"facts": facts})
    monkeypatch.setattr(memory_extract.ram, "is_low", lambda: False)
    return memory_extract.run_once()


def test_learned_facts_are_pending_and_never_recalled_until_approved(review, monkeypatch):
    assert _extract(monkeypatch, ["The user is learning Rust"]) == 1
    [m] = memory_store.list_all()
    assert m["status"] == "pending"
    assert memory_store.list_all(status="active") == [] and len(memory_store.list_all(status="pending")) == 1
    assert memory_store.recall("learning Rust") == []
    assert memory_store.forget("learning Rust") is None  # forget only touches active facts
    assert memory_store.approve(m["id"])
    assert [x["text"] for x in memory_store.recall("learning Rust")] == ["The user is learning Rust"]


def test_review_off_saves_active(monkeypatch):
    from app.core import personalize

    personalize.update({"memory_review": False})
    try:
        assert _extract(monkeypatch, ["The user is learning Rust"]) == 1
    finally:
        personalize.update(personalize.DEFAULTS)
    assert memory_store.list_all()[0]["status"] == "active"


def test_remember_command_stays_active_even_with_review_on(review, client):
    memory_store.add("seed", status="pending")
    client.post("/chat", json={"message": "remember that I study ETE in Raipur"}, headers=AUTH)
    assert any(m["text"].startswith("I study ETE") for m in memory_store.list_all(status="active"))


def test_pending_duplicates_are_not_added_twice(review):
    memory_store.add("I like chess", status="pending")
    assert memory_store.add("I like chess") is None


def test_sensitive_facts_never_become_even_pending(review, monkeypatch):
    sensitive = [
        "The user has diabetes and takes medication",
        "The user earns a salary of 40000 a month",
        "The user's ID number is 1234 5678 9012",
        "The user's national ID is on file",
        "The user's password is hunter2",
        "The user suffers from depression",
    ]
    assert _extract(monkeypatch, sensitive + ["The user prefers short answers"]) == 1
    assert [m["text"] for m in memory_store.list_all()] == ["The user prefers short answers"]


def test_review_api(client, review):
    a = memory_store.add("Likes tea", status="pending")
    b = memory_store.add("Likes chess", status="pending")
    c = memory_store.add("Likes rain", status="pending")
    got = client.get("/memory?status=pending", headers=AUTH).json()
    assert len(got["memories"]) == 3 and got["counts"] == {"active": 0, "pending": 3}
    assert client.get("/memory", headers=AUTH).json()["memories"] == []
    assert client.get("/memory?status=bogus", headers=AUTH).status_code == 400
    assert client.post(f"/memory/{a['id']}/approve", headers=AUTH).status_code == 200
    assert client.post("/memory/99999/approve", headers=AUTH).status_code == 404
    client.delete(f"/memory/{b['id']}", headers=AUTH)  # rejecting is delete
    assert client.post("/memory/approve-all", headers=AUTH).json() == {"approved": 1}
    assert {m["text"] for m in client.get("/memory", headers=AUTH).json()["memories"]} == {"Likes tea", "Likes rain"}
    assert c["id"]
