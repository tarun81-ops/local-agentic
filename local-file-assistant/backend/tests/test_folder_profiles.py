"""Per-folder rules: validation, case-insensitive lookup, allowlist and exclusions in a real
folder, removal from both stores, scan/watcher agreement, caption override, chat style."""
from pathlib import Path

import pytest

from app.api import routes_chat
from app.core import indexer, personalize
from app.core.parsers import image_parser
from app.db import sqlite_fts, vector_store
from tests.conftest import fake_embed


@pytest.fixture(autouse=True)
def _reset():
    personalize.update(personalize.DEFAULTS)
    yield
    personalize.update(personalize.DEFAULTS)


@pytest.fixture
def folder(tmp_path):
    root = (tmp_path / "Docs").resolve()
    (root / "node_modules").mkdir(parents=True)
    (root / "notes.txt").write_text("alpha notes", encoding="utf-8")
    (root / "readme.md").write_text("bravo readme", encoding="utf-8")
    (root / "data.csv").write_text("charlie,1", encoding="utf-8")
    (root / "node_modules" / "dep.txt").write_text("delta dependency", encoding="utf-8")
    return root


def _names(db):
    conn = sqlite_fts.connect(db)
    try:
        return sorted(Path(p).name for p in sqlite_fts.indexed_paths(conn))
    finally:
        conn.close()


def _vector_names(vdb):
    table = vector_store.open_table(vector_store.connect(vdb))
    if table is None:
        return []
    return sorted({Path(r["path"]).name for r in vector_store.search(table, fake_embed(["alpha bravo charlie delta"])[0], limit=50)})


def test_profile_is_validated_and_normalized(tmp_path):
    p = personalize.clean_folder_profile({"label": " College ", "extensions": ["PDF", "*.docx", ".pdf"], "exclude_globs": [" *.tmp "], "answer_style": "study"})
    assert p["label"] == "College" and p["extensions"] == [".pdf", ".docx"] and p["exclude_globs"] == ["*.tmp"]
    for bad in ({"extensions": ["p d f"]}, {"answer_style": "poetic"}, {"label": "x" * 61}, {"exclude_globs": [""]}, {"caption_images": "yes"}, "nope"):
        with pytest.raises(ValueError):
            personalize.clean_folder_profile(bad)


def test_lookup_is_case_insensitive_on_windows(folder):
    personalize.set_folder_profile(folder, {"label": "College", "answer_style": "study"})
    assert personalize.folder_profile(str(folder).lower())["label"] == "College"
    assert personalize.folder_profile(str(folder).upper())["answer_style"] == "study"
    assert personalize.folder_profile(folder.parent) == {}


def test_allowlist_and_exclusions_in_a_real_folder(folder, db_paths):
    db, vdb = db_paths
    personalize.set_folder_profile(folder, {"extensions": ["txt", "md"], "exclude_globs": ["node_modules"]})
    counts = indexer.index_folder(folder, db, vdb)
    assert _names(db) == ["notes.txt", "readme.md"] and counts["seen"] == 2


def test_empty_allowlist_means_every_type(folder, db_paths):
    db, vdb = db_paths
    indexer.index_folder(folder, db, vdb)
    assert _names(db) == ["data.csv", "dep.txt", "notes.txt", "readme.md"]


def test_newly_excluded_files_leave_both_stores(folder, db_paths):
    db, vdb = db_paths
    indexer.index_folder(folder, db, vdb)
    assert "dep.txt" in _names(db) and "dep.txt" in _vector_names(vdb)
    personalize.set_folder_profile(folder, {"extensions": ["txt"], "exclude_globs": ["node_modules/*"]})
    counts = indexer.index_folder(folder, db, vdb)
    assert counts["removed"] == 3  # data.csv, readme.md, dep.txt
    assert _names(db) == ["notes.txt"] and _vector_names(vdb) == ["notes.txt"]


def test_watcher_path_agrees_with_the_scan(folder, db_paths):
    db, vdb = db_paths
    indexer.index_folder(folder, db, vdb)
    personalize.set_folder_profile(folder, {"extensions": ["txt"], "exclude_globs": ["node_modules"]})
    # what the watcher calls for a changed file:
    assert indexer.index_file(folder / "readme.md", db, vdb) == "ignored"  # wrong type: dropped from the index
    assert indexer.index_file(folder / "node_modules" / "dep.txt", db, vdb) == "ignored"
    assert _names(db) == ["data.csv", "notes.txt"]  # only the files touched were re-judged
    (folder / "notes.txt").write_text("alpha changed", encoding="utf-8")
    assert indexer.index_file(folder / "notes.txt", db, vdb) == "indexed"


def test_glob_matches_names_and_paths(tmp_path):
    root = tmp_path
    prof = {"exclude_globs": ["*.tmp", "drafts/*", "Old"]}
    assert not indexer.is_allowed(root / "a.tmp", root, prof)
    assert not indexer.is_allowed(root / "drafts" / "x" / "y.txt", root, prof)
    assert not indexer.is_allowed(root / "old" / "y.txt", root, prof)  # folder name, any case
    assert indexer.is_allowed(root / "keep" / "y.txt", root, prof)


def test_caption_override_per_folder(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(image_parser, "caption", lambda p: calls.append(p) or "a cat")
    monkeypatch.setattr(image_parser, "available", lambda: False)
    img = tmp_path / "a.png"
    img.write_bytes(b"x")
    monkeypatch.setattr(image_parser.settings, "caption_images", False)
    image_parser.parse_image(img)
    assert not calls  # global off
    token = image_parser.CAPTION_OVERRIDE.set(True)
    try:
        assert image_parser.parse_image(img)  # folder on
    finally:
        image_parser.CAPTION_OVERRIDE.reset(token)
    assert len(calls) == 1
    monkeypatch.setattr(image_parser.settings, "caption_images", True)
    token = image_parser.CAPTION_OVERRIDE.set(False)
    try:
        image_parser.parse_image(img)  # folder off beats global on
    finally:
        image_parser.CAPTION_OVERRIDE.reset(token)
    assert len(calls) == 1
    assert indexer._caption_override({"ocr_only": True, "caption_images": True}) is False
    assert indexer._caption_override({"caption_images": None}) is None


def test_folder_answer_style_is_found_by_root_or_top_result(folder, db_paths, monkeypatch):
    db, vdb = db_paths
    monkeypatch.setattr(routes_chat.settings, "db_path", db)
    indexer.index_folder(folder, db, vdb)
    personalize.set_folder_profile(folder, {"answer_style": "study"})
    assert routes_chat._folder_style(str(folder), []) == "study"
    assert routes_chat._folder_style(None, [{"path": str(folder / "notes.txt")}]) == "study"
    assert routes_chat._folder_style(None, [{"path": str(folder.parent / "elsewhere.txt")}]) == ""
    assert routes_chat._folder_style(None, []) == ""


def test_saving_a_profile_rescans_the_folder(folder, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import routes_files
    from app.main import app

    started = []
    monkeypatch.setattr(routes_files, "_start_index", lambda folders: started.append(folders) or {"state": "started"})
    auth = {"Authorization": "Bearer test-token"}
    with TestClient(app, base_url="http://127.0.0.1:8756") as c:
        ok = c.put("/files/roots/profile", json={"folder": str(folder), "profile": {"extensions": ["pdf"]}}, headers=auth)
        bad = c.put("/files/roots/profile", json={"folder": str(folder), "profile": {"extensions": ["a b"]}}, headers=auth)
        missing = c.put("/files/roots/profile", json={"folder": str(folder / "nope"), "profile": {}}, headers=auth)
    assert ok.json() == {"profile": {**personalize.clean_folder_profile({"extensions": ["pdf"]})}, "rescan": "started"}
    assert started == [[folder]]
    assert bad.status_code == 400 and missing.status_code == 400
    assert personalize.folder_profile(folder)["extensions"] == [".pdf"]
