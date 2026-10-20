"""First-run setup: /setup/status says what is missing (Ollama down, model missing, ready), the model pull streams
Ollama's progress and can be cancelled, and the setup flag sticks. No real Ollama: its HTTP replies are stubbed."""
import json
import re
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import routes_setup
from app.config import settings
from app.main import app

AUTH = {"Authorization": "Bearer test-token"}
CHAT, EMBED = settings.ollama_model, settings.embedding_model


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(routes_setup.ram, "status", lambda: {"free_mb": 6000, "total_mb": 16000, "low": False})
    state = {"up": False, "models": [], "exe": False}

    def get(url, timeout=None):
        if not state["up"]:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"version": "0.40.6"}, request=httpx.Request("GET", url))

    monkeypatch.setattr(routes_setup.httpx, "get", get)
    monkeypatch.setattr(routes_setup, "ollama_status", lambda: {"connected": state["up"], "models": list(state["models"])})
    monkeypatch.setattr(routes_setup, "_installed_exe", lambda: state["exe"])
    monkeypatch.setattr(routes_setup, "_job", {"status": "idle"})
    return {"state": state, "client": TestClient(app, base_url="http://127.0.0.1:8756")}


def _events(text):
    return [(m.group(1), json.loads(m.group(2))) for m in re.finditer(r"event: (\w+)\ndata: (.*)\n\n", text)]


def test_setup_needs_the_token(env):
    assert env["client"].get("/setup/status").status_code == 401


def test_ollama_missing_offers_the_official_download_page(env):
    s = env["client"].get("/setup/status", headers=AUTH).json()
    assert s["engine"]["state"] == "missing" and s["engine"]["help"]["url"] == "https://ollama.com/download"
    assert s["missing"] == ["engine", "model"] and s["done"] is False


def test_ollama_installed_but_stopped_says_so_without_a_download(env):
    env["state"]["exe"] = True
    e = env["client"].get("/setup/status", headers=AUTH).json()["engine"]
    assert e["state"] == "not running" and "Start menu" in e["detail"] and "help" not in e


def test_model_missing_lists_the_pull(env):
    env["state"].update(up=True, models=[EMBED])
    s = env["client"].get("/setup/status", headers=AUTH).json()
    assert s["missing"] == ["model"] and [n["command"] for n in s["model"]["needed"]] == [f"ollama pull {CHAT}"]


def test_ready_when_both_models_are_pulled(env):
    env["state"].update(up=True, models=[CHAT, EMBED])
    s = env["client"].get("/setup/status", headers=AUTH).json()
    assert s["missing"] == [] and s["engine"]["state"] == "ready"


def test_recommendation_is_never_bigger_than_the_default():
    assert routes_setup.recommended_chat(64000)["name"] == settings.ollama_model
    assert "slow" in routes_setup.recommended_chat(6000)["why"]


def test_pull_progress_is_summed_over_layers():
    layers = {}
    assert routes_setup.apply_progress(layers, {"status": "pulling manifest"}) == (0, 0)
    assert routes_setup.apply_progress(layers, {"status": "pulling a", "digest": "a", "total": 100, "completed": 50}) == (50, 100)
    assert routes_setup.apply_progress(layers, {"status": "pulling b", "digest": "b", "total": 300}) == (50, 400)
    assert routes_setup.apply_progress(layers, {"status": "pulling a", "digest": "a", "total": 100, "completed": 100}) == (100, 400)


def test_pull_streams_progress_then_done(env, monkeypatch):
    env["state"].update(up=True, models=[])
    monkeypatch.setattr(routes_setup.time, "sleep", lambda s: None)
    pulled = []

    def lines(name):
        pulled.append(name)
        yield {"status": "pulling manifest"}
        for done in (0, 500_000_000, 1_000_000_000):
            yield {"status": "pulling x", "digest": "x", "total": 1_000_000_000, "completed": done}
        yield {"status": "success"}

    monkeypatch.setattr(routes_setup, "pull_lines", lines)
    events = _events(env["client"].post("/setup/model/pull", headers=AUTH).text)
    assert events[-1][0] == "done" and events[-1][1]["percent"] == 100
    assert all(d["percent"] < 100 for k, d in events if k == "progress")
    assert pulled == [CHAT, EMBED]


def test_pull_error_from_ollama_is_shown_in_plain_words(env, monkeypatch):
    env["state"].update(up=True, models=[EMBED])
    monkeypatch.setattr(routes_setup, "pull_lines", lambda name: iter([{"error": "pull model manifest: file does not exist"}]))
    kind, data = _events(env["client"].post("/setup/model/pull", headers=AUTH).text)[-1]
    assert kind == "failed" and "click Download to try again" in data["detail"]


def test_cancel_stops_the_pull(env, monkeypatch):
    env["state"].update(up=True, models=[])
    closed = []

    def lines(name):
        try:
            for i in range(10_000):
                time.sleep(0.01)
                yield {"status": "pulling x", "digest": "x", "total": 10_000, "completed": i}
        finally:
            closed.append(name)  # the stream to Ollama is closed, which stops its download

    monkeypatch.setattr(routes_setup, "pull_lines", lines)
    threading.Timer(0.3, lambda: env["client"].post("/setup/model/pull/cancel", headers=AUTH)).start()
    events = _events(env["client"].post("/setup/model/pull", headers=AUTH).text)
    assert events[-1][0] == "cancelled" and closed == [CHAT]


def test_setup_flag(env):
    c = env["client"]
    assert c.get("/setup/status", headers=AUTH).json()["done"] is False
    assert c.post("/setup/done", headers=AUTH).json() == {"done": True}
    assert c.get("/setup/status", headers=AUTH).json()["done"] is True
