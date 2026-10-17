"""RAG over past conversations: indexing, recall, exclusion of the current chat, prompt format,
deletion, and the /chat integration."""
import json

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.assistant import chat_recall, conversation, prompts
from app.db import assistant_db
from app.main import app

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    assistant_db.migrate()
    conn = assistant_db.connect()
    conn.execute("DELETE FROM conversations")
    conn.commit()
    conn.close()
    monkeypatch.setattr(chat_recall, "schedule_embed", lambda: chat_recall.embed_pending())  # no background thread in tests
    yield


@pytest.fixture
def client():
    with TestClient(app, base_url="http://127.0.0.1:8756") as c:
        yield c


def _chat(title: str, *pairs, route: str = "chat") -> int:
    c = conversation.create(title)
    for q, a in pairs:
        conversation.add_messages(c["id"], [("user", q, {}), ("assistant", a, {"route": route})])
    return c["id"]


def _events(body: str):
    return [(m.split("\n", 1)[0].removeprefix("event: "), json.loads(m.split("\n", 1)[1].removeprefix("data: "))) for m in body.strip().split("\n\n")]


def test_recall_finds_the_relevant_earlier_exchange_and_ranks_it_first():
    pune = _chat("Trip", ("Which train goes from Raipur to Pune?", "The Azad Hind Express runs from Raipur to Pune overnight."))
    _chat("Cooking", ("How do I make dal tadka?", "Fry cumin and garlic in ghee, then pour over the cooked dal."))
    chat_recall.embed_pending()
    hits = chat_recall.recall("which train from Raipur to Pune was it")
    assert hits and hits[0]["conv_id"] == pune
    assert "Azad Hind" in hits[0]["text"]
    assert chat_recall.recall("quantum chromodynamics lattice gauge") == []


def test_current_conversation_is_never_recalled():
    cid = _chat("Trip", ("Which train goes from Raipur to Pune?", "The Azad Hind Express."))
    chat_recall.embed_pending()
    assert chat_recall.recall("train Raipur Pune", exclude_conv=cid) == []
    assert chat_recall.recall("train Raipur Pune")


def test_commands_and_empty_answers_are_not_indexed():
    _chat("Mem", ("remember that I like tea", 'Remembered: "I like tea".'), route="memory")
    _chat("Empty", ("what about zebras", "   "))
    conn = assistant_db.connect()
    chat_recall.sync(conn)
    assert conn.execute("SELECT COUNT(*) FROM chat_index").fetchone()[0] == 0
    conn.close()


def test_sync_is_incremental_and_deleting_a_chat_removes_it_from_recall():
    a = _chat("A", ("Explain entropy of a gas", "Entropy counts the microstates of the gas."))
    conn = assistant_db.connect()
    assert chat_recall.sync(conn) == 1 and chat_recall.sync(conn) == 0
    conn.close()
    chat_recall.embed_pending()
    assert chat_recall.recall("entropy of a gas")
    conversation.delete(a)  # cascades through messages to chat_index and its keyword index
    assert chat_recall.recall("entropy of a gas") == []
    conn = assistant_db.connect()
    assert conn.execute("SELECT COUNT(*) FROM chat_index").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM chat_index_fts WHERE chat_index_fts MATCH 'entropy'").fetchone()[0] == 0
    conn.close()


def test_without_an_embedder_keyword_recall_still_works(monkeypatch):
    _chat("A", ("Where is the passport office?", "The passport office is near the railway station."))

    def down(texts, timeout=None):
        raise RuntimeError("embedder down")

    monkeypatch.setattr(chat_recall.vector_search, "embed", down)
    assert chat_recall.embed_pending() == 0  # nothing embedded, nothing crashes
    assert chat_recall.recall("passport office")


def test_changing_the_embedding_model_re_embeds(monkeypatch):
    _chat("A", ("What is the capital of Chhattisgarh?", "Raipur is the capital."))
    chat_recall.embed_pending()
    monkeypatch.setattr(settings, "embedding_model", "another-model")
    assert chat_recall.embed_pending() == 1
    conn = assistant_db.connect()
    assert conn.execute("SELECT embed_model FROM chat_index").fetchone()[0] == "another-model"
    conn.close()


def test_recall_can_be_turned_off(monkeypatch):
    _chat("A", ("Which train goes to Pune?", "The Azad Hind Express."))
    chat_recall.embed_pending()
    monkeypatch.setattr(settings, "chat_recall_k", 0)
    assert chat_recall.recall("train to Pune") == []


def test_with_recall_labels_the_text_as_quoted_data():
    p = prompts.with_recall("And the return trip?", [{"text": "User: train?\nAssistant: Azad Hind.", "created_at": 1_700_000_000}])
    assert p.endswith("And the return trip?") and "not file excerpts" in p and "never cite" in p and "train? / Assistant" in p
    assert prompts.with_recall("hi", []) == "hi"


def test_chat_route_uses_earlier_chats_and_reports_them(client, monkeypatch):
    _chat("Trip", ("Which train goes from Raipur to Pune?", "The Azad Hind Express runs overnight."))
    chat_recall.embed_pending()
    seen = {}

    def fake_stream(prompt, history, model=None, style=""):
        seen["prompt"] = prompt
        yield "It was the Azad Hind Express."
        return False

    import app.api.routes_chat as rc

    monkeypatch.setattr(rc, "chat_stream", fake_stream)
    r = client.post("/chat", json={"message": "which train from Raipur to Pune was that", "mode": "chat"}, headers=AUTH)
    events = dict(_events(r.text))
    assert "Azad Hind" in seen["prompt"] and "Earlier conversations" in seen["prompt"]
    assert events["recall_used"]["chats"][0]["title"] == "Trip"
    assert events["done"]["recall_used"]
    # The new exchange is indexed for next time, and shows in the conversation search endpoint.
    found = client.get("/conversations/search", params={"q": "which train from Raipur to Pune"}, headers=AUTH).json()["results"]
    assert found and found[0]["title"]


def test_chat_route_does_not_fail_when_recall_breaks(client, monkeypatch):
    import app.api.routes_chat as rc

    def boom(*a, **k):
        raise RuntimeError("index damaged")

    monkeypatch.setattr(rc.chat_recall, "recall", boom)
    monkeypatch.setattr(rc, "chat_stream", lambda *a, **k: iter(()))
    r = client.post("/chat", json={"message": "hello there friend", "mode": "chat"}, headers=AUTH)
    assert "done" in dict(_events(r.text))
