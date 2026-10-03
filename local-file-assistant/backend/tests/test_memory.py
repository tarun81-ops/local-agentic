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

    def fake_chat(message, history=None, model=None):
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
