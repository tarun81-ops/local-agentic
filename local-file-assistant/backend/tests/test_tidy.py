"""Tidy-up: rule-based Downloads cleanup (no model), applied only after approval."""
import os
import time

import pytest
from fastapi.testclient import TestClient

from app.api import routes_organize
from app.config import settings
from app.core import known_folders, tidy

NOW = time.time()
DAY = 86400
AUTH = {"Authorization": "Bearer test-token"}


def _make(path, data=b"x", age_days=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    t = NOW - age_days * DAY
    os.utime(path, (t, t))
    return path


@pytest.fixture
def downloads(tmp_path):
    d = tmp_path / "Downloads"
    _make(d / "Electricity_Bill-Sep2026.pdf", b"%PDF bill sept", 10)
    _make(d / "Electricity_Bill-Sep2026 (1).pdf", b"%PDF bill sept", 9)      # duplicate, name with (1)
    _make(d / "old stuff" / "bill copy.pdf", b"%PDF bill sept", 30)           # duplicate in a sub-folder
    _make(d / "photo.JPG", b"jpeg-bytes", 3)
    _make(d / "setup_vscode.exe", b"MZ installer", 50)
    _make(d / "notes.zip", b"PK zip", 2)
    _make(d / "film.mkv", b"v" * (2 * 1024 * 1024), 200)                                    # "large" for the test threshold, old
    _make(d / "fresh_big.mkv", b"w" * (2 * 1024 * 1024), 1)                                 # large but recent: kept
    _make(d / "unknown.xyz", b"?", 1)                                          # unknown type: left in place
    _make(d / "big.iso.crdownload", b"partial", 3)                             # unfinished, old enough
    _make(d / "now.zip.crdownload", b"partial2", 0)                            # still downloading: never touched
    _make(d / "desktop.ini", b"[.ShellClassInfo]", 100)
    _make(d / "Images" / "already_sorted.png", b"png", 5)
    _make(d / "id_rsa", b"key", 5)                                             # credential-like: never listed
    return d


def by_path(plan):
    return {os.path.basename(a.path): a for a in plan.actions}


def test_plan_groups_and_defaults(downloads):
    plan = tidy.propose(downloads, now=NOW, large_bytes=1024, old_days=90)
    acts = by_path(plan)
    assert plan.kind == "tidy"
    # duplicates: the clean name is kept; the loose "(1)" copy is ticked, the one inside a folder is not
    assert acts["Electricity_Bill-Sep2026 (1).pdf"].group == "Duplicates" and acts["Electricity_Bill-Sep2026 (1).pdf"].selected
    assert acts["bill copy.pdf"].group == "Duplicates" and acts["bill copy.pdf"].selected is False
    assert acts["Electricity_Bill-Sep2026.pdf"].op == "move"
    assert acts["Electricity_Bill-Sep2026.pdf"].to.endswith(os.path.join("Documents", "Electricity_Bill-Sep2026.pdf"))
    # unfinished downloads
    assert acts["big.iso.crdownload"].group == "Unfinished downloads"
    assert "now.zip.crdownload" not in acts
    # old large: proposed but not ticked; recent large left alone
    assert acts["film.mkv"].group == "Old large files" and acts["film.mkv"].selected is False
    assert "fresh_big.mkv" in acts and acts["fresh_big.mkv"].group == "Sort by type/Videos"
    # sort by type
    assert acts["photo.JPG"].group == "Sort by type/Images"
    assert acts["setup_vscode.exe"].group == "Sort by type/Installers"
    assert acts["notes.zip"].group == "Sort by type/Archives"
    # left alone
    for name in ("unknown.xyz", "desktop.ini", "already_sorted.png", "id_rsa"):
        assert name not in acts
    assert plan.freed_bytes == len(b"%PDF bill sept") + len(b"partial")  # only what is ticked


def test_name_clash_in_target_folder_gets_a_free_name(downloads):
    _make(downloads / "Images" / "photo.JPG", b"different photo", 1)
    plan = tidy.propose(downloads, now=NOW)
    assert by_path(plan)["photo.JPG"].to.endswith(os.path.join("Images", "photo (2).JPG"))


def test_nothing_on_disk_changes_while_planning(downloads):
    before = sorted(str(p) for p in downloads.rglob("*"))
    tidy.propose(downloads, now=NOW)
    assert sorted(str(p) for p in downloads.rglob("*")) == before


def test_empty_folder(tmp_path):
    (tmp_path / "Downloads").mkdir()
    plan = tidy.propose(tmp_path / "Downloads")
    assert plan.actions == [] and "Nothing to tidy" in plan.summary


@pytest.fixture
def client(monkeypatch, downloads, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "db_path", tmp_path / "data" / "index.db")
    monkeypatch.setattr(settings, "vector_db_dir", tmp_path / "data" / "lancedb")
    (tmp_path / "data").mkdir()
    real_locate = known_folders.locate
    monkeypatch.setattr(known_folders, "locate", lambda key: downloads.resolve() if key == "downloads" else (None if key == "desktop" else real_locate(key)))
    trashed = []
    monkeypatch.setattr(routes_organize, "trash", lambda p: (trashed.append(p), p.unlink()))
    from app.main import app

    with TestClient(app, base_url="http://127.0.0.1:8756") as c:
        c.trashed = trashed
        yield c


def test_api_tidy_apply_only_what_was_ticked_then_undo(client, downloads):
    folders = client.get("/organize/tidy/folders", headers=AUTH).json()["folders"]
    assert folders[0]["key"] == "downloads"
    plan = client.post("/organize/tidy", json={"large_mb": 1}, headers=AUTH).json()  # Downloads by default, even though it isn't indexed
    chosen = [a for a in plan["actions"] if a["selected"]]
    assert not any(a["group"] == "Old large files" for a in chosen)
    res = client.post("/organize/apply", json={"actions": chosen}, headers=AUTH).json()
    assert all(r["ok"] for r in res["results"]), res
    assert (downloads / "Installers" / "setup_vscode.exe").is_file()
    assert (downloads / "film.mkv").is_file()  # unticked: untouched
    assert len(client.trashed) == 2  # the loose "(1)" copy and the unfinished download; not the copy in a folder
    assert (downloads / "old stuff" / "bill copy.pdf").is_file()
    undone = client.post("/organize/undo", json={"batch": res["batch"]}, headers=AUTH).json()
    assert all(r["ok"] for r in undone["results"])
    assert (downloads / "setup_vscode.exe").is_file()


def test_api_tidy_refuses_other_folders(client, tmp_path):
    other = tmp_path / "Windows"
    other.mkdir()
    r = client.post("/organize/tidy", json={"folder": str(other)}, headers=AUTH)
    assert r.status_code == 400
    bad = {"op": "delete", "path": str(_make(other / "x.dll"))}
    assert client.post("/organize/apply", json={"actions": [bad]}, headers=AUTH).status_code == 400


def test_duplicates_inside_unzipped_programs_are_never_ticked(tmp_path):
    d = tmp_path / "Downloads"
    _make(d / "MyApp" / "lib" / "core.dll", b"same-dll-bytes")
    _make(d / "OtherApp" / "core.dll", b"same-dll-bytes")
    _make(d / "MyApp" / "cache" / "build.tmp", b"t", 5)
    plan = tidy.propose(d, now=NOW)
    deletes = [a for a in plan.actions if a.op == "delete"]
    assert deletes and not any(a.selected for a in deletes)
    assert all("check" in (a.reason or "") for a in deletes)


def test_the_copy_you_filed_in_a_folder_is_kept_and_the_loose_one_binned(tmp_path):
    d = tmp_path / "Downloads"
    _make(d / "Project" / "report.pdf", b"same report", 10)
    _make(d / "report.pdf", b"same report", 5)
    acts = by_path(tidy.propose(d, now=NOW))
    a = acts["report.pdf"]
    assert a.op == "delete" and a.path == str(d / "report.pdf") and a.selected


def test_numbered_names_are_not_mistaken_for_copies(tmp_path):
    d = tmp_path / "Downloads"
    _make(d / "Lecture_1.pdf", b"same", 10)
    _make(d / "Lecture_1 (1).pdf", b"same", 5)
    assert by_path(tidy.propose(d, now=NOW))["Lecture_1 (1).pdf"].group == "Duplicates"


def test_onedrive_online_only_files_are_never_read_or_offered(tmp_path, monkeypatch):
    d = tmp_path / "Desktop"
    _make(d / "a.iso", b"z" * 4096, 400)
    _make(d / "b.iso", b"z" * 4096, 400)
    files, _ = tidy.scan(d)
    for f in files:
        f["cloud"] = True  # what Windows reports for Files On-Demand placeholders
    monkeypatch.setattr(tidy, "scan", lambda folder: (files, False))
    read = []
    monkeypatch.setattr(tidy, "_quick_key", lambda p: read.append(p))
    plan = tidy.propose(d, now=NOW, large_bytes=1024)
    assert read == []
    assert not any(a.op == "delete" for a in plan.actions)


def test_last_copy_of_a_big_duplicate_is_never_offered_for_deletion(tmp_path):
    # Two copies of an old, large file: one is binned as a duplicate, so the kept one must not also
    # be listed under "Old large files" (ticking both would delete every copy).
    d = tmp_path / "Downloads"
    big = b"i" * 4096
    _make(d / "ISOs" / "ubuntu.iso", big, 300)  # kept: already filed in a folder
    _make(d / "ubuntu.iso", big, 300)           # loose copy: binned as a duplicate
    plan = tidy.propose(d, now=NOW, large_bytes=1024, old_days=90)
    deletes = [a for a in plan.actions if a.op == "delete"]
    assert [a.path for a in deletes] == [str(d / "ubuntu.iso")]
    assert deletes[0].group == "Duplicates"


def test_a_big_old_file_without_copies_is_still_offered(tmp_path):
    d = tmp_path / "Downloads"
    _make(d / "ISOs" / "ubuntu.iso", b"i" * 4096, 300)
    acts = by_path(tidy.propose(d, now=NOW, large_bytes=1024, old_days=90))
    assert acts["ubuntu.iso"].group == "Old large files" and acts["ubuntu.iso"].selected is False
