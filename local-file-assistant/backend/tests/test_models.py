"""Settings > Model (Ollama build): Ollama's models with size, expected RAM and tags plus the downloadable catalog;
remove refuses the model in use and asks Ollama to delete another; a catalog pull uses setup's job; the optional other
server is off by default, needs a second confirmation when it isn't on this PC, and then answers chat."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import routes_models, routes_setup
from app.config import settings
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
    remote = {"enabled": True, "url": "http://192.168.1.20:1234/v1", "model": "qwen"}
    r = c.put("/models/external", json=remote, headers=AUTH)
    assert r.status_code == 400 and "not this PC" in r.json()["detail"] and external.active() is None
    assert c.put("/models/external", json={**remote, "confirm_remote": True}, headers=AUTH).json()["enabled"]
    assert client.current_model() == "qwen" and str(client.get_client().base_url).startswith("http://192.168.1.20:1234/v1")
    c.put("/models/external", json={"enabled": False}, headers=AUTH)
    assert client.current_model() == settings.ollama_model
