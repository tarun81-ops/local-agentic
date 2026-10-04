"""Saved searches (collections) and the recent-files list on the Chat home."""
import os
import time

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core import indexer
from app.core.assistant import learning, saved_searches
from app.db import assistant_db
from app.main import app

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def client():
    with TestClient(app, base_url="http://127.0.0.1:8756") as c:
        yield c


@pytest.fixture(autouse=True)
def clean(client):
    conn = assistant_db.connect()
    conn.execute("DELETE FROM collections")
    conn.execute("DELETE FROM file_opens")
    conn.commit()
    conn.close()
    yield


@pytest.fixture
def folder(tmp_path):
    root = (tmp_path / "notes").resolve()
    root.mkdir()
    (root / "alpha.txt").write_text("quarterly budget forecast for the robotics club", encoding="utf-8")
    (root / "beta.txt").write_text("recipe for tea and biscuits", encoding="utf-8")
    (root / "gamma.txt").write_text("old trip itinerary to Raipur", encoding="utf-8")
    old = time.time() - 30 * 86400
    os.utime(root / "gamma.txt", (old, old))
    indexer.index_folder(root, settings.db_path, settings.vector_db_dir)
    yield root
    indexer.remove_root(str(root), settings.db_path, settings.vector_db_dir)


def _mine(client, folder, limit_note=None):
    """The recent list, limited to this test's folder (other tests leave files in the shared index)."""
    files = client.get("/files/recent", headers=AUTH).json()["files"]
    return [f for f in files if f["path"].startswith(str(folder))]


def test_crud_validation_and_pinned_first(client):
    a = client.post("/collections", json={"name": "Budget", "query": "budget forecast"}, headers=AUTH).json()
    b = client.post("/collections", json={"name": "Tea", "query": "tea recipe", "pinned": False}, headers=AUTH).json()
    assert a["pinned"] and not b["pinned"]
    assert [c["name"] for c in client.get("/collections", headers=AUTH).json()["collections"]] == ["Budget", "Tea"]
    client.patch(f"/collections/{b['id']}", json={"pinned": True, "name": " Tea  time "}, headers=AUTH)
    got = client.get("/collections", headers=AUTH).json()["collections"]
    assert [c["name"] for c in got] == ["Tea time", "Budget"] and all(c["pinned"] for c in got)  # newest pinned first
    assert client.post("/collections", json={"name": "", "query": "x"}, headers=AUTH).status_code == 422
    assert client.post("/collections", json={"name": "x", "query": "q" * 201}, headers=AUTH).status_code == 422
    assert client.patch("/collections/9999", json={"pinned": True}, headers=AUTH).status_code == 404
    assert client.patch(f"/collections/{a['id']}", json={"query": " "}, headers=AUTH).status_code == 422
    client.delete(f"/collections/{a['id']}", headers=AUTH)
    assert [c["name"] for c in client.get("/collections", headers=AUTH).json()["collections"]] == ["Tea time"]


def test_run_searches_the_saved_query_and_root(client, folder):
    c = client.post("/collections", json={"name": "Budget", "query": "budget forecast", "root": str(folder)}, headers=AUTH).json()
    out = client.get(f"/collections/{c['id']}/run", headers=AUTH).json()
    assert out["collection"]["id"] == c["id"] and out["results"][0]["name"] == "alpha.txt"
    elsewhere = client.post("/collections", json={"name": "Nowhere", "query": "budget forecast", "root": str(folder / "missing")}, headers=AUTH).json()
    assert client.get(f"/collections/{elsewhere['id']}/run", headers=AUTH).json()["results"] == []
    assert client.get("/collections/9999/run", headers=AUTH).status_code == 404


def test_recent_lists_opens_first_then_changes_without_duplicates(client, folder):
    learning.record_open(str(folder / "beta.txt"), "tea")
    time.sleep(0.01)
    learning.record_open(str(folder / "alpha.txt"), "budget")
    files = _mine(client, folder)
    assert [(f["name"], f["reason"]) for f in files] == [("alpha.txt", "opened"), ("beta.txt", "opened")]  # gamma is 30 days old
    learning.record_open(str(folder / "gamma.txt"), "trip")
    names = [f["name"] for f in _mine(client, folder)]
    assert names == ["gamma.txt", "alpha.txt", "beta.txt"] and len(set(names)) == 3


def test_recent_changes_fill_in_when_nothing_was_opened(client, folder):
    files = _mine(client, folder)
    assert {f["name"] for f in files} == {"alpha.txt", "beta.txt"} and {f["reason"] for f in files} == {"changed"}


def test_recent_drops_files_no_longer_indexed(client, folder):
    learning.record_open(str(folder / "alpha.txt"), "budget")
    learning.record_open(r"C:\gone\deleted.txt", "x")
    names = [f["name"] for f in client.get("/files/recent", headers=AUTH).json()["files"]]
    assert "deleted.txt" not in names and "alpha.txt" in names
    indexer.remove_file(folder / "alpha.txt", settings.db_path, settings.vector_db_dir)
    assert "alpha.txt" not in [f["name"] for f in client.get("/files/recent", headers=AUTH).json()["files"]]


def test_learning_wipe_clears_recent_opens_but_keeps_collections(client, folder):
    saved_searches.create("Keep me", "budget")
    learning.record_open(str(folder / "gamma.txt"), "trip")
    assert "gamma.txt" in [f["name"] for f in client.get("/files/recent", headers=AUTH).json()["files"]]
    client.post("/learning/wipe", headers=AUTH)
    assert "gamma.txt" not in [f["name"] for f in client.get("/files/recent", headers=AUTH).json()["files"]]
    assert [c["name"] for c in saved_searches.list_all()] == ["Keep me"]
