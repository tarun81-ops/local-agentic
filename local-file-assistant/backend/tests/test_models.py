"""Settings > Model (Ollama build): Ollama's models with size, expected RAM and tags plus the downloadable catalog;
remove refuses the model in use and asks Ollama to delete another; a catalog pull uses setup's job; the optional other
server is off by default, needs a second confirmation when it isn't on this PC, and then answers chat."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import routes_models, routes_setup
from app.config import settings
from app.core import prefs
from app.core.llm import client, external
from app.main import app

AUTH = {"Authorization": "Bearer test-token"}
TAGS = [{"name": settings.ollama_model, "size": 1_889_000_000}, {"name": settings.embedding_model, "size": 562_000_000},
        {"name": "llama3.2:1b", "size": 1_300_000_000}]


@pytest.fixture
def c(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(routes_models, "_tags", lambda: list(TAGS))
    monkeypatch.setattr(routes_models.ram, "status", lambda: {"free_mb": 6000, "total_mb": 16000, "low": False})
    return TestClient(app, base_url="http://127.0.0.1:8756")


def test_lists_ollama_models_and_the_catalog(c):
    models = {m["name"]: m for m in c.get("/models", headers=AUTH).json()["models"]}
    default = models[settings.ollama_model]
    assert default["installed"] and default["active"] and default["recommended"] and default["size_mb"] == 1889
    assert models[settings.embedding_model]["kind"] == "embed"
    big = models["qwen3-vl:4b-instruct"]
    assert not big["installed"] and not big["recommended"] and big["ram_mb"] > default["ram_mb"]
    assert c.get("/models", headers=AUTH).json()["device"] is None


def test_remove_refuses_models_in_use_and_asks_ollama_for_others(c, monkeypatch):
    for name in (settings.ollama_model, settings.embedding_model):
        assert c.post("/models/remove", json={"name": name}, headers=AUTH).status_code == 400
    calls = []
    monkeypatch.setattr(routes_models.httpx, "request", lambda method, url, json=None, timeout=None: (calls.append((method, url, json)), httpx.Response(200))[1])
    assert c.post("/models/remove", json={"name": "llama3.2:1b"}, headers=AUTH).status_code == 200
    assert calls == [("DELETE", f"{settings.ollama_url}/api/delete", {"model": "llama3.2:1b"})]


def test_a_catalog_pull_goes_through_setups_job(c, monkeypatch):
    monkeypatch.setattr(routes_setup, "ollama_status", lambda: {"connected": True, "models": [m["name"] for m in TAGS]})
    monkeypatch.setattr(routes_setup, "_job", {"status": "idle"})
    monkeypatch.setattr(routes_setup.time, "sleep", lambda s: None)
    pulled = []
    monkeypatch.setattr(routes_setup, "pull_lines", lambda name: (pulled.append(name), iter([{"status": "success"}]))[1])
    body = c.post("/setup/model/pull", json={"repo": "qwen3-vl:4b-instruct"}, headers=AUTH).text
    assert pulled == ["qwen3-vl:4b-instruct"] and "event: done" in body
    assert c.post("/setup/model/pull", json={"repo": "some/other-model"}, headers=AUTH).status_code == 400


def test_other_server_is_off_by_default_and_remote_needs_confirmation(c):
    assert external.active() is None
    remote = {"enabled": True, "url": "https://192.168.1.20:1234/v1", "model": "qwen"}
    r = c.put("/models/external", json=remote, headers=AUTH)
    assert r.status_code == 400 and "not this PC" in r.json()["detail"] and external.active() is None
    assert c.put("/models/external", json={**remote, "confirm_remote": True}, headers=AUTH).json()["enabled"]
    assert client.current_model() == "qwen" and str(client.get_client().base_url).startswith("https://192.168.1.20:1234/v1")
    c.put("/models/external", json={"enabled": False}, headers=AUTH)
    assert client.current_model() == settings.ollama_model


def test_a_server_on_another_computer_must_use_https_even_when_confirmed(c):
    plain = {"enabled": True, "url": "http://192.168.1.20:1234/v1", "model": "qwen", "confirm_remote": True}
    r = c.put("/models/external", json=plain, headers=AUTH)
    assert r.status_code == 400 and "https://" in r.json()["detail"] and external.active() is None
    prefs.set("external_server", {"enabled": True, "url": plain["url"], "model": "qwen"})  # saved before this rule existed
    assert external.active() is None  # is not used either
    prefs.set("external_server", {})


def test_only_answers_and_flashcards_reach_the_other_server(c, monkeypatch, tmp_path):
    """With the other server on, a recorder stands in for both servers: captions, the briefing, the file agent's
    planning and choosing, and the plug-in tool planner all go to Ollama; a chat answer and flashcards go out."""
    from types import SimpleNamespace

    from app.core.assistant import briefing, file_agent
    from app.core.assistant.tools import mcp, registry
    from app.core.llm import answerer
    from app.core.parsers import image_parser

    external.set(True, "http://127.0.0.1:1234/v1", "lm-model")
    sent = []

    def recorder(local=False):
        def create(**kw):
            sent.append((local, kw["model"]))
            if kw.get("stream"):
                return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="ok"), finish_reason="stop")])])
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"keywords": ["fee"], "pick": [1], "calls": []}'))])
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setattr(client, "get_client", recorder)
    monkeypatch.setattr(answerer, "get_client", recorder)  # imported by name there
    monkeypatch.setattr(briefing, "settings", lambda: {"use_model": True})
    monkeypatch.setattr(briefing.ram, "is_low", lambda: False)
    monkeypatch.setattr(mcp, "start_background", lambda: None)
    tool = {"name": "count_words", "description": "Count the words in some text", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}
    mcp._state["rec"] = {"tools": [tool], "error": ""}
    mcp._register("rec", tool, False)
    try:
        png = tmp_path / "r.png"
        png.write_bytes(b"\x89PNG")
        image_parser.caption(png)
        briefing.phrase({"tasks": [], "events": [], "overdue": [], "files": {"count": 0, "names": []}})
        file_agent.understand("my fee receipt")
        cands = [{"name": f"fee {i}.pdf", "folder": str(tmp_path), "modified": "", "path": str(tmp_path / f"fee {i}.pdf")} for i in (1, 2)]
        file_agent.choose("my fee receipt", cands)
        mcp.plan("count the words in this")
    finally:
        registry.REGISTRY.pop("mcp.rec.count_words", None)
        mcp._state.pop("rec", None)
    local_calls, sent[:] = list(sent), []
    assert len(local_calls) == 5 and all(local and model == settings.ollama_model for local, model in local_calls)

    list(answerer.chat_stream("hello"))
    client.llm_json("make flashcards", local=False)  # what routes_study sends
    assert sent == [(False, "lm-model"), (False, "lm-model")]
    external.set(False)


def test_with_the_other_server_on_ollamas_own_model_stays_protected(c, monkeypatch):
    from app.core.llm import idle

    external.set(True, "http://127.0.0.1:1234/v1", "lm-model")
    r = c.post("/models/remove", json={"name": settings.ollama_model}, headers=AUTH)
    assert r.status_code == 400 and "in use" in r.json()["detail"]
    assert {m["name"]: m for m in c.get("/models", headers=AUTH).json()["models"]}[settings.ollama_model]["active"]
    assert routes_setup.plan([]) and all(p["name"] != "lm-model" for p in routes_setup.plan([]))  # setup never pulls it
    posted = []
    monkeypatch.setattr(idle.httpx, "post", lambda url, json=None, timeout=None: posted.append(json))
    monkeypatch.setattr(idle.settings, "llm_provider", "ollama")
    idle.unload()
    assert posted == [{"model": settings.ollama_model, "keep_alive": 0}]  # unloads Ollama's model, not the other one
    external.set(False)


def test_remove_only_asks_ollama_about_a_model_it_lists(c, monkeypatch):
    monkeypatch.setattr(routes_models.httpx, "request", lambda *a, **k: pytest.fail("must not call Ollama"))
    r = c.post("/models/remove", json={"name": "not-there:7b"}, headers=AUTH)
    assert r.status_code == 404 and "isn't downloaded" in r.json()["detail"]
    monkeypatch.setattr(routes_models, "_tags", lambda: [])
    r = c.post("/models/remove", json={"name": "llama3.2:1b"}, headers=AUTH)
    assert r.status_code == 503 and "Start Ollama" in r.json()["detail"]
