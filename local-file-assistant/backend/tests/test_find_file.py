""""Find that file": description search without the chat model."""
import os
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core import find_file, indexer, pc_access

NOW = datetime(2026, 10, 14, 12, 0)
DAY = 86400


def _age(path, days):
    t = NOW.timestamp() - days * DAY
    os.utime(path, (t, t))


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "db_path", tmp_path / "data" / "index.db")
    monkeypatch.setattr(settings, "vector_db_dir", tmp_path / "data" / "lancedb")
    (tmp_path / "data").mkdir()
    root = tmp_path / "Documents"
    (root / "Bills").mkdir(parents=True)
    (root / "Internship").mkdir()
    files = {
        "Bills/Electricity_Bill-Sep2026.txt": ("CSPDCL electricity bill. Amount due 1,240. Billing month September 2026.", 20),
        "Bills/Electricity_Bill-Jun2026.txt": ("CSPDCL electricity bill. Amount due 980. Billing month June 2026.", 120),
        "Bills/Water_Bill-Sep2026.txt": ("Municipal water bill for September.", 21),
        "Internship/Offer_Letter_Acme.txt": ("We are pleased to offer you the position of Summer Intern at Acme.", 60),
        "scan_0042.txt": ("Internship offer letter. Dear Tarun, we are pleased to offer you an internship.", 30),
        "notes.txt": ("Lecture notes on semiconductors and diodes.", 5),
    }
    for rel, (text, age) in files.items():
        p = root / rel
        p.write_text(text, encoding="utf-8")
        _age(p, age)
    indexer.index_folder(root, settings.db_path, settings.vector_db_dir)
    pc_access.set_config(enabled=False)
    return root


def names(out):
    return [r["name"] for r in out["results"]]


def test_understand_pulls_out_type_and_date_without_a_model():
    plan = find_file.understand("the electricity bill pdf from last month", NOW)
    assert plan["words"] == ["electricity", "bill"]
    assert plan["exts"] == [".pdf"]
    assert datetime.fromtimestamp(plan["after"]) == datetime(2026, 9, 1)  # start of last month, runs to now


def test_last_month_bill_beats_the_older_one(home):
    out = find_file.find("the electricity bill from last month", now=NOW)
    assert names(out)[0] == "Electricity_Bill-Sep2026.txt"
    assert "Electricity_Bill-Jun2026.txt" not in names(out)  # outside the window
    assert any("name has" in w for w in out["results"][0]["why"])


def test_offer_letter_found_by_name_and_by_text(home):
    out = find_file.find("my internship offer letter", now=NOW)
    top = names(out)[:2]
    assert set(top) == {"Offer_Letter_Acme.txt", "scan_0042.txt"}  # one by its name+folder, one only by what it says
    scan = next(r for r in out["results"] if r["name"] == "scan_0042.txt")
    assert "text inside matches" in scan["why"]


def test_wrong_type_guess_is_relaxed_not_empty(home):
    out = find_file.find("electricity bill pdf", now=NOW)
    assert out["relaxed"] == ["type"] and names(out)[0].startswith("Electricity_Bill")


def test_date_window_relaxed_when_nothing_fits(home):
    out = find_file.find("water bill from yesterday", now=NOW)
    assert out["relaxed"] == ["date"] and names(out)[0] == "Water_Bill-Sep2026.txt"


def test_moved_files_never_returned(home):
    (home / "Bills" / "Water_Bill-Sep2026.txt").unlink()
    assert "Water_Bill-Sep2026.txt" not in names(find_file.find("water bill", now=NOW))


def test_works_with_the_content_search_down(home):
    def broken(description, plan):
        return [], False

    out = find_file.find("electricity bill", now=NOW, content=broken)
    assert set(names(out)[:2]) == {"Electricity_Bill-Sep2026.txt", "Electricity_Bill-Jun2026.txt"}


def test_api_find_open_and_reveal_refuse_unknown_files(home, tmp_path):
    from app.main import app

    client = TestClient(app, base_url="http://127.0.0.1:8756", headers={"Authorization": "Bearer test-token"})
    r = client.get("/find", params={"q": "electricity bill"})
    assert r.status_code == 200 and r.json()["results"]
    outside = tmp_path / "secret.txt"
    outside.write_text("x")
    assert client.post("/find/reveal", json={"path": str(outside)}).status_code == 404
    assert client.post("/find/open", json={"path": str(outside)}).status_code == 404


def test_pc_catalog_names_join_in_when_pc_access_is_on(home, tmp_path):
    drive = tmp_path / "drive"
    (drive / "Downloads").mkdir(parents=True)
    pdf = drive / "Downloads" / "internship_offer_letter_signed.pdf"
    pdf.write_bytes(b"%PDF-1.4 not read")  # never indexed: found by its name only
    pc_access.set_config(enabled=True, drives=[str(drive)])
    pc_access.scan([str(drive)])
    out = find_file.find("internship offer letter pdf", now=NOW)
    assert names(out)[0] == "internship_offer_letter_signed.pdf"
    assert find_file.allowed_to_open(str(pdf))
    pc_access.set_config(enabled=False)
    assert not find_file.allowed_to_open(str(pdf))


def test_empty_description():
    assert find_file.find("   ")["results"] == []


def test_wrong_type_guess_still_finds_a_catalog_only_file(home, tmp_path):
    drive = tmp_path / "drive2"
    drive.mkdir()
    (drive / "internship_offer_letter.docx").write_bytes(b"never read")
    pc_access.set_config(enabled=True, drives=[str(drive)])
    pc_access.scan([str(drive)])
    out = find_file.find("internship offer letter pdf", now=NOW, content=lambda d, p: ([], False))
    assert "internship_offer_letter.docx" in names(out) and out["relaxed"] == ["type"]
    pc_access.set_config(enabled=False)


def test_best_name_match_survives_thousands_of_near_matches(home):
    old = home / "class_10_marksheet.txt"
    old.write_text("x")
    _age(old, 2000)
    noise = home / "noise"
    noise.mkdir()
    for i in range(400):  # newer files matching only one word ("result" is a marksheet synonym)
        (noise / f"result_note_{i}.txt").write_text("y")
    indexer.index_folder(home, settings.db_path, settings.vector_db_dir)
    out = find_file.find("class 10 marksheet", now=NOW, content=lambda d, p: ([], False))
    assert names(out)[0] == "class_10_marksheet.txt"


def test_words_from_the_indexed_folder_path_match_nothing(home):
    # The indexed folder sits under a path containing "documents"; that must not make every file match.
    out = find_file.find("documents", now=NOW, content=lambda d, p: ([], False))
    assert out["results"] == []


def test_whole_pc_contents_stay_closed_while_pc_access_is_off(home, tmp_path):
    from app.db import sqlite_fts

    drive = tmp_path / "pcdrive"
    drive.mkdir()
    f = drive / "electricity_bill_scan.txt"
    f.write_text("electricity bill")
    conn = sqlite_fts.connect(settings.db_path)
    try:
        sqlite_fts.add_root(conn, drive)
        conn.execute("UPDATE roots SET hidden = 1 WHERE path = ?", (str(drive),))
        conn.commit()
    finally:
        conn.close()
    indexer.index_file(f, settings.db_path, settings.vector_db_dir, root=drive)
    pc_access.set_config(enabled=False)
    assert "electricity_bill_scan.txt" not in names(find_file.find("electricity bill", now=NOW))
    assert not find_file.allowed_to_open(str(f))


def test_find_uses_no_model(home, monkeypatch):
    import app.core.search.vector_search as vs

    def boom(*a, **k):
        raise AssertionError("embedding model called")

    monkeypatch.setattr(vs, "embed", boom)
    assert names(find_file.find("internship offer letter", now=NOW))


def _hidden_drive_with_matches(tmp_path, n):
    from app.db import sqlite_fts

    drive = tmp_path / "pcdrive_many"
    drive.mkdir()
    conn = sqlite_fts.connect(settings.db_path)
    with conn:
        sqlite_fts.add_root(conn, drive, hidden=True)
    conn.close()
    for i in range(n):
        f = drive / f"electricity_bill_copy_{i}.txt"
        f.write_text("y")
        indexer.index_file(f, settings.db_path, settings.vector_db_dir, root=drive)
    return drive


def test_hidden_pc_matches_never_crowd_out_indexed_files(home, tmp_path):
    # More hidden whole-PC name matches than the 300-row pool: the indexed bills must still be found.
    _hidden_drive_with_matches(tmp_path, find_file.INDEX_NAME_POOL + 50)
    no_content = lambda d, p: ([], False)  # noqa: E731
    pc_access.set_config(enabled=False)
    out = find_file.find("electricity bill", now=NOW, content=no_content)
    assert {"Electricity_Bill-Sep2026.txt", "Electricity_Bill-Jun2026.txt"} <= set(names(out))
    assert not any(n.startswith("electricity_bill_copy_") for n in names(out))  # hidden while off
