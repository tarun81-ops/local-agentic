import pytest
from fastapi.testclient import TestClient

from app.core import prefs
from app.core.assistant import forms
from app.main import app

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def library(tmp_path, monkeypatch):
    """Four real files in an 'index' (list_files is faked), and a content search that finds one."""
    names = {"Priya_Resume_2026.pdf": 100, "resume_old.docx": 50, "passport_scan.jpg": 200, "notes.txt": 10}
    rows = []
    for i, (name, mtime) in enumerate(names.items()):
        p = tmp_path / name
        p.write_text("x")
        rows.append({"path": str(p), "root": str(tmp_path), "hash": str(i), "size": 1, "mtime": float(mtime)})
    monkeypatch.setattr(forms.sqlite_fts, "list_files", lambda conn, root=None, limit=5000: rows)
    hit = {"path": rows[3]["path"], "match": "semantic", "snippet": "curriculum vitae of Priya"}
    monkeypatch.setattr(forms, "hybrid_search", lambda q, root=None, limit=6: {"results": [hit]})
    prefs.set("ask_each_time_globs", [])
    return tmp_path


def test_name_matches_come_first_then_content_matches(library):
    out = forms.suggest("Upload your resume")
    assert [r["name"] for r in out] == ["Priya_Resume_2026.pdf", "resume_old.docx", "notes.txt"]  # newest name match first
    assert [r["match"] for r in out] == ["name", "name", "semantic"] and out[2]["snippet"].startswith("curriculum")


def test_accept_filters_by_extension_mime_and_wildcard(library):
    assert [r["name"] for r in forms.suggest("resume", accept=".pdf")] == ["Priya_Resume_2026.pdf"]
    assert [r["name"] for r in forms.suggest("resume", accept="application/pdf,.docx")] == ["Priya_Resume_2026.pdf", "resume_old.docx"]
    assert [r["name"] for r in forms.suggest("passport photo", accept="image/*")] == ["passport_scan.jpg"]
    assert forms.accepts("a.pdf", None) and not forms.accepts("a.pdf", ".png")


def test_filler_words_do_not_match_everything_and_empty_labels_return_nothing(library):
    assert forms.words("Upload your file here (optional)") == []
    assert forms.suggest("Upload your file here") == []
    assert [r["name"] for r in forms.suggest("", page_title="Resume submission form")][:1] == ["Priya_Resume_2026.pdf"]


def test_ask_every_time_paths_are_flagged_not_hidden(library):
    prefs.set("ask_each_time_globs", ["*/passport*"])
    try:
        out = {r["name"]: r["sensitive"] for r in forms.suggest("passport")}
        assert out == {"passport_scan.jpg": True, "notes.txt": False}  # flagged, still listed
    finally:
        prefs.set("ask_each_time_globs", [])


def test_files_that_vanished_are_skipped_and_search_failures_fall_back_to_names(library, monkeypatch):
    (library / "resume_old.docx").unlink()
    monkeypatch.setattr(forms, "hybrid_search", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("locked")))
    assert [r["name"] for r in forms.suggest("resume")] == ["Priya_Resume_2026.pdf"]


def test_endpoint(library):
    with TestClient(app, base_url="http://127.0.0.1:8756") as c:
        r = c.post("/forms/suggest", json={"label": "Upload resume", "accept": ".pdf"}, headers=AUTH)
    assert r.status_code == 200 and [x["name"] for x in r.json()["results"]] == ["Priya_Resume_2026.pdf"]
