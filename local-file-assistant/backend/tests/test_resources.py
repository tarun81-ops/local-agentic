"""RAM guard, idle model unloading and the OpenAI-compatible (OpenVINO) embedding path."""
import httpx

from app.config import settings
from app.core import indexer, memory
from app.core.llm import idle
from app.core.search import vector_search
from app.db import sqlite_fts


def test_low_memory_indexes_keywords_only_and_retries_later(corpus, db_paths, monkeypatch):
    db, vdb = db_paths
    monkeypatch.setattr(memory, "is_low", lambda: True)
    counts = indexer.index_folder(corpus, db, vdb)
    assert counts["indexed"] == 2
    conn = sqlite_fts.connect(db)
    try:
        hashes = {r[0] for r in conn.execute("SELECT hash FROM files")}
    finally:
        conn.close()
    assert hashes == {""}  # blank = vectors still owed; next scan retries
    assert any("low memory" in e["error"] for e in indexer.RECENT_ERRORS)

    monkeypatch.setattr(memory, "is_low", lambda: False)
    again = indexer.index_folder(corpus, db, vdb)
    assert again["indexed"] == 2 and again["skipped"] == 0


def test_idle_unload_timing(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "llm_idle_unload_s", 600)
    monkeypatch.setattr(idle.httpx, "post", lambda url, json, timeout: calls.append(json))
    idle._last_used = None
    assert not idle._due(10_000)  # never used: nothing loaded by us
    idle.touch()
    t = idle._last_used
    assert not idle._due(t + 599)
    assert idle._due(t + 601)
    assert idle.unload() and calls[-1]["keep_alive"] == 0
    assert not idle._due(t + 10_000)  # after unloading, wait for the next use


def test_idle_unload_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "llm_idle_unload_s", 0)
    idle.touch()
    assert not idle._due(idle._last_used + 1e9)


def test_openai_compatible_embeddings(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "ollama_base_url", "http://127.0.0.1:9000/v3")
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]})

    real_client = httpx.Client
    monkeypatch.setattr(vector_search.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    from tests.conftest import REAL_EMBED

    out = REAL_EMBED(["a", "b"])
    assert seen["url"] == "http://127.0.0.1:9000/v3/embeddings"
    assert out == [[1.0], [2.0]]  # re-ordered by index


def test_memory_status_shape():
    s = memory.status()
    assert set(s) == {"free_mb", "total_mb", "low"}
