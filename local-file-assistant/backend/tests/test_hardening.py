"""Organizer edge cases (links, case, long paths, bad names, partial failures) and API
hardening (Host header, loopback-only binding)."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

from app.api import routes_files, routes_organize
from app.core.file_ops import file_mover
from app.core.llm.organizer import clean_folder
from app.main import app
from app.models.organize import OrganizeAction
from run import is_loopback

AUTH = {"Authorization": "Bearer test-token"}


def _problems(actions, root):
    return routes_organize._validate(actions, [str(root)])[1]


def test_link_inside_the_folder_is_not_moved(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.pdf").write_text("x")
    root = tmp_path / "root"
    root.mkdir()
    link = root / "link.pdf"
    try:
        os.symlink(outside / "secret.pdf", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    problems = _problems([OrganizeAction(op="delete", path=str(link))], root)
    assert problems and "link" in problems[0]
    assert (outside / "secret.pdf").exists()


def test_destinations_differing_only_in_case_collide(tmp_path):
    (tmp_path / "a.pdf").write_text("a")
    (tmp_path / "b.pdf").write_text("b")
    actions = [
        OrganizeAction(op="move", path=str(tmp_path / "a.pdf"), to=str(tmp_path / "Finance" / "x.pdf")),
        OrganizeAction(op="move", path=str(tmp_path / "b.pdf"), to=str(tmp_path / "finance" / "X.PDF")),
    ]
    problems = _problems(actions, tmp_path)
    assert len(problems) == 1 and "already taken" in problems[0]


def test_destination_too_long_for_windows_is_refused(tmp_path):
    (tmp_path / "a.pdf").write_text("a")
    deep = tmp_path / ("d" * 120) / ("e" * 120) / "a.pdf"
    problems = _problems([OrganizeAction(op="move", path=str(tmp_path / "a.pdf"), to=str(deep))], tmp_path)
    assert problems and "too long" in problems[0]


@pytest.mark.parametrize("folder", ["Invoices?", "CON", "Notes.", 'Q3 "final"', "a|b"])
def test_names_windows_rejects(tmp_path, folder):
    (tmp_path / "a.pdf").write_text("a")
    to = tmp_path / folder / "a.pdf"
    assert _problems([OrganizeAction(op="move", path=str(tmp_path / "a.pdf"), to=str(to))], tmp_path)
    assert clean_folder(folder) is None  # and the model can't suggest them in the first place


def test_valid_names_still_pass():
    assert clean_folder("Finance/Invoices 2026") == "Finance/Invoices 2026"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(routes_organize, "trash", lambda p: p.unlink())
    with TestClient(app, base_url="http://127.0.0.1:8756") as c:
        yield c


def test_partial_failure_is_reported_and_undo_restores_what_moved(client, tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    for n in ("a", "b"):
        (root / f"{n}.pdf").write_text(n)
    monkeypatch.setattr(routes_files, "_start_index", lambda folders: {"state": "started"})  # no scan needed
    assert client.post("/files/roots", json={"folder": str(root)}, headers=AUTH).status_code == 200

    real = file_mover._raw_move

    def locked(src, dst):
        if src.name == "b.pdf":
            raise PermissionError("The process cannot access the file because it is being used by another process")
        real(src, dst)

    monkeypatch.setattr(file_mover, "_raw_move", locked)
    actions = [
        {"op": "move", "path": str(root / "a.pdf"), "to": str(root / "Moved" / "a.pdf")},
        {"op": "move", "path": str(root / "b.pdf"), "to": str(root / "Moved" / "b.pdf")},
    ]
    out = client.post("/organize/apply", json={"actions": actions}, headers=AUTH).json()
    assert [r["ok"] for r in out["results"]] == [True, False]
    assert "used by another process" in out["results"][1]["error"]

    monkeypatch.setattr(file_mover, "_raw_move", real)
    undone = client.post("/organize/undo", json={"batch": out["batch"]}, headers=AUTH).json()["results"]
    assert len(undone) == 1 and undone[0]["ok"]  # only the move that happened is undone
    assert (root / "a.pdf").exists() and (root / "b.pdf").exists()


def test_foreign_host_header_is_refused(client):
    # DNS rebinding: a page on evil.example re-pointed at 127.0.0.1 still sends its own Host.
    r = client.get("/search", params={"q": "x"}, headers={**AUTH, "Host": "evil.example:8756"})
    assert r.status_code == 421
    assert client.get("/health", headers={"Host": "evil.example"}).status_code == 421
    assert client.get("/health", headers={"Host": "localhost:8756"}).status_code == 200


def test_file_origin_of_the_packaged_app_is_allowed(client):
    r = client.get("/files/roots", headers={**AUTH, "Origin": "file://"})
    assert r.status_code == 200


def test_backend_only_binds_to_loopback():
    assert is_loopback("127.0.0.1") and is_loopback("localhost") and is_loopback("::1")
    assert not is_loopback("0.0.0.0") and not is_loopback("192.168.1.5")


def test_cancel_when_idle_is_a_no_op(client):
    assert client.post("/files/index/cancel", headers=AUTH).json() == {"cancelling": False}


def test_startup_rescans_unscanned_folders(tmp_path, monkeypatch):
    from app.config import settings
    from app.db import sqlite_fts

    folder = tmp_path / "never_scanned"
    folder.mkdir()
    conn = sqlite_fts.connect(settings.db_path)
    try:
        sqlite_fts.add_root(conn, folder)
    finally:
        conn.close()
    started = []
    monkeypatch.setattr(routes_files, "_start_index", lambda folders: started.extend(folders))
    assert str(folder) in routes_files.rescan_unscanned()
    assert folder in started
