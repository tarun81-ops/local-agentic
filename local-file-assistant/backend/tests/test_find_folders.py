"""Find returns folders too. Regression: "blackmagic folder" found nothing, because the PC list held
files only, and the files inside DaVinci's "Blackmagic Design" folder aren't document types."""
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core import find_file, pc_access


@pytest.fixture
def drive(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "db_path", tmp_path / "data" / "index.db")
    (tmp_path / "data").mkdir()
    d = tmp_path / "C"
    bm = d / "Users" / "me" / "OneDrive" / "Documents" / "Blackmagic Design" / "DaVinci Resolve"
    bm.mkdir(parents=True)
    (bm / "Resolve.db").write_bytes(b"x")  # not a document type: never listed
    (d / "Users" / "me" / "Documents").mkdir(parents=True)
    (d / "Users" / "me" / "Documents" / "blackmagic_invoice.pdf").write_bytes(b"%PDF")
    (d / "Users" / "me" / ".ssh").mkdir()
    pc_access.set_config(enabled=True, drives=[str(d)])
    pc_access.scan([str(d)])
    yield d
    pc_access.set_config(enabled=False)


def _folder(drive):
    return drive / "Users" / "me" / "OneDrive" / "Documents" / "Blackmagic Design"


def test_folders_are_listed_but_only_find_asks_for_them(drive):
    assert [r["name"] for r in pc_access.find(["blackmagic"])] == ["blackmagic_invoice.pdf"]  # Chat, forms
    names = [r["name"] for r in pc_access.find(["blackmagic"], folders=True)]
    assert "Blackmagic Design" in names
    s = pc_access.status()
    assert s["files"] == 1 and s["folders"] >= 5  # the count you see is files only
    assert not any(".ssh" in r["path"] for r in pc_access.find(["ssh"], folders=True))


def test_blackmagic_folder_is_found_first_when_you_say_folder(drive):
    out = find_file.find("blackmagic folder", content=lambda d, p: ([], False))
    top = out["results"][0]
    assert top["name"] == "Blackmagic Design" and top["is_folder"] and top["ext"] == pc_access.FOLDER
    assert top["size"] is None
    assert out["understood"]["type_label"] == "FOLDER" and out["understood"]["words"] == ["blackmagic"]
    assert out["relaxed"] == []


def test_plain_name_search_finds_the_folder_too(drive):
    out = find_file.find("blackmagic", content=lambda d, p: ([], False))
    assert {"Blackmagic Design", "blackmagic_invoice.pdf"} <= {r["name"] for r in out["results"]}
    assert out["results"][0]["name"] == "blackmagic_invoice.pdf"  # a file named like it still wins


def test_a_folder_opens_in_explorer_but_is_never_read(drive):
    folder = str(_folder(drive))
    assert find_file.allowed_to_open(folder, folders=True)
    assert not find_file.allowed_to_open(folder)  # Study / reading paths
    ok, _ = pc_access.can_read(folder)
    assert not ok
    pc_access.set_config(enabled=False)
    assert not find_file.allowed_to_open(folder, folders=True)


def test_find_open_route_accepts_a_found_folder(drive, monkeypatch):
    from app.api import routes_find
    from app.main import app

    opened = []
    monkeypatch.setattr(routes_find.sys, "platform", "linux")
    monkeypatch.setattr(routes_find.subprocess, "Popen", lambda args, *a, **k: opened.append(args))
    client = TestClient(app, base_url="http://127.0.0.1:8756", headers={"Authorization": "Bearer test-token"})
    r = client.post("/find/open", json={"path": str(_folder(drive))})
    assert r.status_code == 200, r.text
    assert opened and str(_folder(drive)) in opened[0]
