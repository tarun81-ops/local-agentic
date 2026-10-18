"""Organize → Move & delete: browse a folder, then move / copy / rename / create / delete files and folders.
Every batch is checked first (all or nothing), moves/copies/new folders can be undone, deletes go to the
Recycle Bin, and main folders, system folders and credential files are never touched."""
import os
import pathlib
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from app.api import routes_organize
from app.config import settings
from app.core import known_folders, pc_access, tidy
from app.core.file_ops import manager, safe_delete

AUTH = {"Authorization": "Bearer test-token"}


def _make(path, data=b"x", age_days=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    t = time.time() - age_days * 86400
    os.utime(path, (t, t))
    return path


@pytest.fixture
def pc(tmp_path, monkeypatch):
    drive = tmp_path / "C"
    home = drive / "Users" / "me"
    for name in ("Desktop", "Downloads", "Documents", "Pictures", "Videos", "Music"):
        (home / name).mkdir(parents=True)
    _make(home / "Downloads" / "bill.pdf", b"%PDF bill")
    _make(home / "Downloads" / "photo.jpg", b"jpg")
    _make(home / "Downloads" / "desktop.ini", b"[x]")
    _make(home / "Downloads" / "passwords.txt", b"secret")
    _make(home / "Documents" / "bill.pdf", b"%PDF other bill")  # same name as the one in Downloads
    _make(home / "Documents" / "Project" / "notes.txt", b"notes")
    _make(home / "Documents" / "Project" / "sub" / "a.txt", b"a")
    (drive / "Windows" / "System32").mkdir(parents=True)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "db_path", tmp_path / "data" / "index.db")
    monkeypatch.setattr(settings, "vector_db_dir", tmp_path / "data" / "lancedb")
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(known_folders, "locate", lambda key: (home / key.capitalize()).resolve())
    monkeypatch.setattr(manager, "_locate", lambda key, fid: (home / key.capitalize()).resolve())
    trashed = []

    def fake_trash(p):
        p = pathlib.Path(p)
        trashed.append(p)
        shutil.rmtree(p) if p.is_dir() else p.unlink()

    monkeypatch.setattr(routes_organize, "trash", fake_trash)
    monkeypatch.setattr(safe_delete, "trash", fake_trash)
    pc_access.set_config(enabled=True, drives=[str(drive)])
    yield {"drive": drive, "home": home, "trashed": trashed}
    pc_access.set_config(enabled=False)


@pytest.fixture
def client(pc):
    from app.main import app

    with TestClient(app, base_url="http://127.0.0.1:8756") as c:
        yield c


def _apply(client, *actions):
    return client.post("/organize/apply", json={"actions": list(actions)}, headers=AUTH)


def test_places_and_browse_list_only_what_is_safe(client, pc):
    places = client.get("/organize/places", headers=AUTH).json()["places"]
    assert [p["label"] for p in places][:3] == ["Desktop", "Downloads", "Documents"] and places[-1]["kind"] == "drive"
    r = client.get("/organize/browse", params={"path": str(pc["home"] / "Documents")}, headers=AUTH).json()
    assert [e["name"] for e in r["entries"]] == ["Project", "bill.pdf"]  # folders first
    assert r["entries"][0]["is_dir"] and r["entries"][0]["size"] is None
    assert r["parent"] == str(pc["home"].resolve())
    names = [e["name"] for e in client.get("/organize/browse", params={"path": str(pc["home"] / "Downloads")}, headers=AUTH).json()["entries"]]
    assert "desktop.ini" not in names and "passwords.txt" not in names and "bill.pdf" in names
    assert client.get("/organize/browse", params={"path": str(pc["drive"] / "Windows")}, headers=AUTH).status_code == 403


def test_browse_needs_pc_access_outside_the_everyday_folders(client, pc):
    pc_access.set_config(enabled=False)
    r = client.get("/organize/browse", params={"path": str(pc["home"] / "Documents" / "Project")}, headers=AUTH)
    assert r.status_code == 403 and "PC access" in r.json()["detail"]


def test_move_files_to_a_folder_keeps_both_on_a_name_clash_and_undoes(client, pc):
    dl, docs = pc["home"] / "Downloads", pc["home"] / "Documents"
    res = _apply(client, {"op": "move", "path": str(dl / "bill.pdf"), "to_folder": str(docs)},
                 {"op": "move", "path": str(dl / "photo.jpg"), "to_folder": str(docs)}).json()
    assert all(r["ok"] for r in res["results"]), res
    assert (docs / "bill (2).pdf").read_bytes() == b"%PDF bill"  # the existing bill.pdf is never overwritten
    assert (docs / "bill.pdf").read_bytes() == b"%PDF other bill" and (docs / "photo.jpg").is_file()
    undone = client.post("/organize/undo", json={"batch": res["batch"]}, headers=AUTH).json()["results"]
    assert all(r["ok"] for r in undone) and (dl / "bill.pdf").is_file() and not (docs / "bill (2).pdf").exists()


def test_move_a_folder_but_never_into_itself_or_a_main_folder(client, pc):
    docs = pc["home"] / "Documents"
    bad = _apply(client, {"op": "move", "path": str(docs / "Project"), "to_folder": str(docs / "Project" / "sub")})
    assert bad.status_code == 400 and "inside itself" in str(bad.json())
    main = _apply(client, {"op": "move", "path": str(pc["home"] / "Downloads"), "to_folder": str(docs)})
    assert main.status_code == 400 and "main folder" in str(main.json())
    ok = _apply(client, {"op": "move", "path": str(docs / "Project"), "to_folder": str(pc["home"] / "Desktop")}).json()
    assert ok["results"][0]["ok"] and (pc["home"] / "Desktop" / "Project" / "sub" / "a.txt").is_file()


def test_copy_then_undo_sends_only_the_copy_to_the_recycle_bin(client, pc):
    docs, desk = pc["home"] / "Documents", pc["home"] / "Desktop"
    res = _apply(client, {"op": "copy", "path": str(docs / "Project"), "to_folder": str(desk)},
                 {"op": "copy", "path": str(docs / "bill.pdf"), "to_folder": str(docs)}).json()  # copy in place: "bill (2).pdf"
    assert all(r["ok"] for r in res["results"]), res
    assert (desk / "Project" / "notes.txt").is_file() and (docs / "bill (2).pdf").is_file()
    client.post("/organize/undo", json={"batch": res["batch"]}, headers=AUTH)
    assert not (desk / "Project").exists() and not (docs / "bill (2).pdf").exists()
    assert (docs / "Project" / "notes.txt").is_file() and (docs / "bill.pdf").is_file()  # originals untouched
    assert {p.name for p in pc["trashed"]} == {"Project", "bill (2).pdf"}


def test_new_folder_then_move_into_it_in_one_batch_and_undo_removes_both(client, pc):
    docs = pc["home"] / "Documents"
    new = docs / "Bills 2026"
    res = _apply(client, {"op": "move", "path": str(docs / "bill.pdf"), "to_folder": str(new)},
                 {"op": "mkdir", "path": str(new)}).json()  # order in the request doesn't matter
    assert all(r["ok"] for r in res["results"]), res
    assert (new / "bill.pdf").is_file()
    client.post("/organize/undo", json={"batch": res["batch"]}, headers=AUTH)
    assert (docs / "bill.pdf").is_file() and not new.exists()
    assert all(b["batch"] != res["batch"] for b in client.get("/organize/history", headers=AUTH).json()["batches"])


def test_rename_checks_windows_names_and_clashes(client, pc):
    docs = pc["home"] / "Documents"
    assert _apply(client, {"op": "rename", "path": str(docs / "bill.pdf"), "new_name": "bill:2.pdf"}).status_code == 400
    _make(docs / "Project" / "notes2.txt")
    assert _apply(client, {"op": "rename", "path": str(docs / "Project" / "notes.txt"), "new_name": "notes2.txt"}).status_code == 400
    res = _apply(client, {"op": "rename", "path": str(docs / "Project"), "new_name": "College project"}).json()
    assert res["results"][0]["ok"] and (docs / "College project" / "notes.txt").is_file()
    client.post("/organize/undo", json={"batch": res["batch"]}, headers=AUTH)
    assert (docs / "Project" / "notes.txt").is_file()


def test_delete_goes_to_the_recycle_bin_and_one_bad_action_changes_nothing(client, pc):
    docs, dl = pc["home"] / "Documents", pc["home"] / "Downloads"
    bad = _apply(client, {"op": "delete", "path": str(docs / "Project")},
                 {"op": "delete", "path": str(dl / "passwords.txt")})  # credential-like: refused
    assert bad.status_code == 400 and (docs / "Project").is_dir() and pc["trashed"] == []
    res = _apply(client, {"op": "delete", "path": str(docs / "Project")}, {"op": "delete", "path": str(dl / "photo.jpg")}).json()
    assert all(r["ok"] for r in res["results"]) and {p.name for p in pc["trashed"]} == {"Project", "photo.jpg"}
    b = client.get("/organize/history", headers=AUTH).json()["batches"][0]
    assert b["trashed"] == 2 and b["undoable"] == 0
    assert _apply(client, {"op": "delete", "path": str(pc["drive"])}).status_code == 400  # a whole drive: never


def test_tidy_finds_empty_folders_outermost_only_and_reports_space_by_type(pc):
    d = pc["home"] / "Downloads"
    (d / "empty1" / "inner" / "deeper").mkdir(parents=True)  # listed once, as empty1
    (d / "kept" / "empty2").mkdir(parents=True)
    _make(d / "kept" / "file.txt")
    _make(d / "onlyini" / "desktop.ini")  # nothing but Windows' own file: empty
    _make(d / "video.mp4", b"v" * 5000)
    plan = tidy.propose(d)
    empties = sorted(pathlib.Path(a.path).name for a in plan.actions if a.group == "Empty folders")
    assert empties == ["empty1", "empty2", "onlyini"]
    ticked = sorted(pathlib.Path(a.path).name for a in plan.actions if a.group == "Empty folders" and a.selected)
    assert ticked == ["empty1", "onlyini"]  # empty2 sits inside a folder: listed, not ticked
    assert plan.by_type[0]["type"] == "Videos" and plan.by_type[0]["bytes"] == 5000
    assert "2 empty folders to remove" in plan.summary


def test_tidy_checks_can_be_switched_off_and_any_pc_folder_works(client, pc):
    d = pc["home"] / "Documents" / "Project"
    (d / "empty").mkdir()
    only_empty = client.post("/organize/tidy", json={"folder": str(d), "checks": ["empty"]}, headers=AUTH).json()
    assert {a["group"] for a in only_empty["actions"]} == {"Empty folders"}
    assert client.post("/organize/tidy", json={"folder": str(d), "checks": ["duplicates"]}, headers=AUTH).json()["actions"] == []
    assert client.post("/organize/tidy", json={"folder": str(d), "checks": []}, headers=AUTH).status_code == 400
    assert client.post("/organize/tidy", json={"folder": str(pc["drive"])}, headers=AUTH).status_code == 400  # whole drive


def test_open_and_reveal_only_what_the_page_may_show(client, pc, monkeypatch):
    opened = []
    monkeypatch.setattr(routes_organize.sys, "platform", "linux")
    monkeypatch.setattr(routes_organize.subprocess, "Popen", lambda args, *a, **k: opened.append(args))
    assert client.post("/organize/open", json={"path": str(pc["home"] / "Documents" / "Project")}, headers=AUTH).status_code == 200
    assert client.post("/organize/reveal", json={"path": str(pc["home"] / "Documents" / "bill.pdf")}, headers=AUTH).status_code == 200
    assert client.post("/organize/open", json={"path": str(pc["home"] / "Downloads" / "passwords.txt")}, headers=AUTH).status_code == 403
    assert client.post("/organize/open", json={"path": str(pc["drive"] / "Windows")}, headers=AUTH).status_code == 403
    assert len(opened) == 2


def test_a_folder_move_across_drives_that_fails_leaves_the_original_and_no_half_copy(tmp_path, monkeypatch):
    import errno

    from app.core.file_ops import file_mover

    src = tmp_path / "A" / "Project"
    _make(src / "one.txt")
    _make(src / "two.txt")
    dst = tmp_path / "B" / "Project"

    def cross_drive(a, b):
        raise OSError(errno.EXDEV, "cross-device link")

    def broken_copy(a, b, **k):
        os.makedirs(b)
        _make(pathlib.Path(b) / "one.txt")
        raise OSError("disk full")

    monkeypatch.setattr(file_mover.os, "rename", cross_drive)
    monkeypatch.setattr(file_mover.shutil, "copytree", broken_copy)
    with pytest.raises(OSError):
        file_mover.move(src, dst, tmp_path / "log", "b1")
    assert (src / "one.txt").is_file() and (src / "two.txt").is_file()
    assert not dst.exists()
    assert file_mover.history(tmp_path / "log") == []


# ---------- review fixes ----------

def test_folders_that_contain_a_main_folder_are_protected_too(client, pc):
    """Review #1: deleting C:\\Users (the parent of home) was allowed while PC access was on."""
    users = pc["home"].parent
    _make(users / "Public" / "readme.txt")
    for action in ({"op": "delete", "path": str(users)}, {"op": "rename", "path": str(users), "new_name": "U"},
                   {"op": "move", "path": str(users), "to_folder": str(pc["drive"] / "Windows")}):
        r = _apply(client, action)
        assert r.status_code == 400 and "main folder" in str(r.json()), action
    assert users.is_dir() and pc["trashed"] == []
    ok = _apply(client, {"op": "delete", "path": str(users / "Public")}).json()  # a sibling that holds no main folder
    assert ok["results"][0]["ok"]


def test_programs_and_scripts_are_never_opened_only_shown(client, pc, monkeypatch):
    """Review #2: Open ran .exe/.bat/.ps1/.lnk files through os.startfile."""
    opened = []
    monkeypatch.setattr(routes_organize.sys, "platform", "linux")
    monkeypatch.setattr(routes_organize.subprocess, "Popen", lambda args, *a, **k: opened.append(args))
    dl = pc["home"] / "Downloads"
    for name in ("setup.exe", "run.bat", "tool.PS1", "game.lnk"):
        _make(dl / name, b"MZ")
        r = client.post("/organize/open", json={"path": str(dl / name)}, headers=AUTH)
        assert r.status_code == 400 and "Show in Explorer" in r.json()["detail"], name
    assert opened == []
    assert client.post("/organize/reveal", json={"path": str(dl / "setup.exe")}, headers=AUTH).status_code == 200
    entries = {e["name"]: e for e in client.get("/organize/browse", params={"path": str(dl)}, headers=AUTH).json()["entries"]}
    assert entries["setup.exe"]["runnable"] and not entries["bill.pdf"]["runnable"]


def test_rename_refuses_slashes_instead_of_dropping_them(client, pc):
    """Review #3: "sub/evil.exe" was quietly renamed to "evil.exe"."""
    docs = pc["home"] / "Documents"
    for bad in ("sub/new.pdf", "..\\..\\new.pdf", "a\\b.pdf"):
        r = _apply(client, {"op": "rename", "path": str(docs / "bill.pdf"), "new_name": bad})
        assert r.status_code == 400 and "\\ or /" in str(r.json()), bad
    assert (docs / "bill.pdf").is_file()
    ok = _apply(client, {"op": "rename", "path": str(docs / "bill.pdf"), "new_name": "Bill Oct.pdf"}).json()
    assert ok["results"][0]["ok"] and (docs / "Bill Oct.pdf").is_file()


def test_a_failed_file_copy_leaves_no_partial_file(tmp_path, monkeypatch):
    """Review #5: a half-written copy stayed behind when a file copy failed."""
    from app.core.file_ops import file_mover

    src = _make(tmp_path / "a" / "big.iso", b"data")
    dst = tmp_path / "b" / "big.iso"

    def broken(a, b, **k):
        pathlib.Path(b).write_bytes(b"da")
        raise OSError("disk full")

    monkeypatch.setattr(file_mover.shutil, "copy2", broken)
    with pytest.raises(OSError):
        file_mover.copy(src, dst, tmp_path / "log", "b1")
    assert not dst.exists() and src.read_bytes() == b"data" and file_mover.history(tmp_path / "log") == []


def test_copies_and_cross_drive_moves_over_the_size_limit_are_refused(client, pc, monkeypatch):
    """Review #6: copies were limited by file count only, so one 40 GB file blocked the app for minutes."""
    monkeypatch.setattr(manager, "MAX_COPY_BYTES", 1000)
    docs = pc["home"] / "Documents"
    _make(docs / "video.mp4", b"v" * 2000)
    r = _apply(client, {"op": "copy", "path": str(docs / "video.mp4"), "to_folder": str(pc["home"] / "Desktop")})
    assert r.status_code == 400 and "File Explorer" in str(r.json())
    # A move on the same drive is a rename: instant, so no limit.
    assert _apply(client, {"op": "move", "path": str(docs / "video.mp4"), "to_folder": str(pc["home"] / "Desktop")}).json()["results"][0]["ok"]
    monkeypatch.setattr(manager, "_same_drive", lambda a, b: False)  # pretend Desktop is on another drive
    r = _apply(client, {"op": "move", "path": str(pc["home"] / "Desktop" / "video.mp4"), "to_folder": str(docs)})
    assert r.status_code == 400 and "File Explorer" in str(r.json())


def test_copying_a_folder_with_password_or_key_files_is_refused(client, pc):
    """Review #8: the credential rule only covered files picked directly; copying their folder spread them."""
    proj = pc["home"] / "Documents" / "Project"
    _make(proj / "config" / ".env", b"API_KEY=x")
    r = _apply(client, {"op": "copy", "path": str(proj), "to_folder": str(pc["home"] / "Desktop")})
    assert r.status_code == 400 and "password or key" in str(r.json())
    assert not (pc["home"] / "Desktop" / "Project").exists()


def test_empty_folders_inside_sub_folders_and_app_folders_are_not_ticked(pc):
    """Review #7: empty folders apps create (Custom Office Templates, Zoom) were ticked for the Recycle Bin."""
    d = pc["home"] / "Documents"
    (d / "Custom Office Templates").mkdir()
    (d / "Zoom").mkdir()
    (d / "old empty").mkdir()
    (d / "Project" / "empty inside").mkdir()
    plan = tidy.propose(d, checks=["empty"])
    by_name = {pathlib.Path(a.path).name: a for a in plan.actions}
    assert "Custom Office Templates" not in by_name and "Zoom" not in by_name
    assert by_name["old empty"].selected is True
    assert by_name["empty inside"].selected is False and "inside a folder" in by_name["empty inside"].reason


def test_open_uses_chat_s_blocked_list_too_and_refuses_stream_names(client, pc, monkeypatch):
    """Second review: Organize's runnable list missed types chat's open tool blocks (.msp, .chm, macro files),
    and an NTFS stream name like "a.txt:b.exe" would hide its type."""
    from app.core.assistant.tools.builtin import BLOCKED_EXT

    monkeypatch.setattr(routes_organize.sys, "platform", "linux")
    monkeypatch.setattr(routes_organize.subprocess, "Popen", lambda *a, **k: None)
    assert all(manager.is_runnable(f"x{ext}") for ext in BLOCKED_EXT)
    dl = pc["home"] / "Downloads"
    for name in ("patch.msp", "help.chm", "budget.xlsm"):
        _make(dl / name)
        assert client.post("/organize/open", json={"path": str(dl / name)}, headers=AUTH).status_code == 400, name
    r = client.post("/organize/open", json={"path": str(dl / "bill.pdf") + ":evil.exe"}, headers=AUTH)
    assert r.status_code == 400 and "Not a file or folder name" in r.json()["detail"]
    assert client.post("/organize/open", json={"path": str(dl / "bill.pdf")}, headers=AUTH).status_code == 200


# ---- Clean up plans that go stale, duplicate keepers, partial deletes


def test_skip_missing_skips_what_vanished_instead_of_refusing_the_batch(client, pc):
    dl = pc["home"] / "Downloads"
    gone = dl / "gone.pdf"
    acts = [{"op": "move", "path": str(gone), "to_folder": str(pc["home"] / "Documents")},
            {"op": "move", "path": str(dl / "photo.jpg"), "to_folder": str(pc["home"] / "Documents")}]
    refused = client.post("/organize/apply", json={"actions": acts}, headers=AUTH)
    assert refused.status_code == 400 and (dl / "photo.jpg").exists()  # the default stays all-or-nothing
    res = client.post("/organize/apply", json={"actions": acts, "skip_missing": True}, headers=AUTH).json()
    assert [(r["ok"], r.get("skipped", False)) for r in res["results"]] == [(True, True), (True, False)]
    assert not (dl / "photo.jpg").exists() and (pc["home"] / "Documents" / "photo.jpg").exists()


def _dup_plan(client, pc):
    dl = pc["home"] / "Downloads"
    _make(dl / "a.dat", b"same bytes", 5)
    _make(dl / "a (1).dat", b"same bytes", 1)
    plan = client.post("/organize/tidy", json={"checks": ["duplicates"]}, headers=AUTH).json()
    [dup] = [a for a in plan["actions"] if a["op"] == "delete"]
    assert dup["keep"].endswith("a.dat") and dup["path"].endswith("a (1).dat")
    return dl, dup


def test_a_duplicate_is_not_deleted_when_its_kept_copy_is_gone_or_changed(client, pc):
    dl, dup = _dup_plan(client, pc)
    (dl / "a.dat").unlink()  # you deleted the copy that was meant to stay
    r = client.post("/organize/apply", json={"actions": [dup]}, headers=AUTH)
    assert r.status_code == 400 and "gone or has changed" in r.json()["detail"]["problems"][0] and (dl / "a (1).dat").exists()
    r = client.post("/organize/apply", json={"actions": [dup], "skip_missing": True}, headers=AUTH).json()
    assert r["results"][0]["skipped"] and (dl / "a (1).dat").exists()  # the last copy is left alone
    _make(dl / "a.dat", b"different now!")  # the keeper was replaced by other content (other size)
    assert client.post("/organize/apply", json={"actions": [dup]}, headers=AUTH).status_code == 400


def test_the_kept_copy_cannot_be_deleted_in_the_same_batch(client, pc):
    dl, dup = _dup_plan(client, pc)
    both = [dup, {"op": "delete", "path": dup["keep"]}]
    r = client.post("/organize/apply", json={"actions": both}, headers=AUTH)
    assert r.status_code == 400 and "being kept" in r.json()["detail"]["problems"][0]
    assert (dl / "a.dat").exists() and (dl / "a (1).dat").exists()
    ok = client.post("/organize/apply", json={"actions": [dup]}, headers=AUTH).json()
    assert ok["results"][0]["ok"] and (dl / "a.dat").exists() and not (dl / "a (1).dat").exists()


def test_a_folder_that_was_only_partly_deleted_is_logged_as_such(client, pc, monkeypatch):
    folder = pc["home"] / "Documents" / "Project"
    real = routes_organize.erase

    def stuck(path):  # an open file stops it halfway
        (path / "notes.txt").unlink()
        raise PermissionError("in use")

    monkeypatch.setattr(routes_organize, "erase", stuck)
    res = _apply(client, {"op": "delete_forever", "path": str(folder)}).json()
    assert res["results"][0]["ok"] is False and "in use" in res["results"][0]["error"]
    batch = client.get("/organize/history", headers=AUTH).json()["batches"][0]
    assert batch["purged"] == 1 and batch["partial"] == 1
    monkeypatch.setattr(routes_organize, "erase", lambda p: (_ for _ in ()).throw(PermissionError("locked")))  # nothing removed: nothing logged
    _apply(client, {"op": "delete_forever", "path": str(folder / "sub")})
    assert client.get("/organize/history", headers=AUTH).json()["batches"][0]["batch"] == batch["batch"]
    monkeypatch.setattr(routes_organize, "erase", real)


def _wait_job(client, job, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(f"/organize/tidy/jobs/{job}", headers=AUTH).json()
        if r["state"] != "running":
            return r
        time.sleep(0.05)
    raise AssertionError("scan did not finish")


def test_a_background_scan_reports_progress_and_hands_over_the_plan(client, pc):
    dl = pc["home"] / "Downloads"
    _make(dl / "x.dat", b"y" * 3000, 5); _make(dl / "x (1).dat", b"y" * 3000, 1)
    job = client.post("/organize/tidy/start", json={"checks": ["duplicates", "sort"]}, headers=AUTH).json()["job"]
    done = _wait_job(client, job)
    assert done["state"] == "done" and done["error"] is None
    assert any(a["group"] == "Duplicates" for a in done["plan"]["actions"]) and done["plan"]["scanned"] >= 2
    assert client.get("/organize/tidy/jobs/nope", headers=AUTH).status_code == 404
    assert client.post("/organize/tidy/start", json={"checks": []}, headers=AUTH).status_code == 400  # same checks as /tidy
    assert client.post("/organize/tidy/start", json={"folder": str(pc["drive"])}, headers=AUTH).status_code == 400


def test_a_background_scan_can_be_stopped_and_reports_progress_ticks(client, pc, monkeypatch):
    real = tidy.scan

    def slow_scan(folder, tick=None):
        for n in range(200):
            tick("Looking through folders", n, None)  # raises Cancelled once stopped
            time.sleep(0.02)
        return real(folder)

    monkeypatch.setattr(tidy, "scan", slow_scan)
    job = client.post("/organize/tidy/start", json={}, headers=AUTH).json()["job"]
    time.sleep(0.2)
    running = client.get(f"/organize/tidy/jobs/{job}", headers=AUTH).json()
    assert running["state"] == "running" and running["phase"] == "Looking through folders" and running["done"] > 0
    assert client.post(f"/organize/tidy/jobs/{job}/cancel", headers=AUTH).json()["state"] in ("running", "cancelled")
    assert _wait_job(client, job)["state"] == "cancelled"
    newer = client.post("/organize/tidy/start", json={}, headers=AUTH).json()["job"]  # a new scan stops an older one
    older = client.post("/organize/tidy/start", json={}, headers=AUTH).json()["job"]
    assert _wait_job(client, newer)["state"] == "cancelled"
    client.post(f"/organize/tidy/jobs/{older}/cancel", headers=AUTH)
    _wait_job(client, older)


def test_hashing_reports_bytes_so_one_huge_file_still_moves_the_bar(tmp_path):
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"z" * 2_500_000); b.write_bytes(b"z" * 2_500_000)
    files = [{"path": str(p), "size": p.stat().st_size, "depth": 0, "mtime": 0, "cloud": False} for p in (a, b)]
    seen = []
    assert len(tidy.duplicate_groups(files, lambda phase, done, total: seen.append((phase, done, total)))) == 1
    hashing = [s for s in seen if s[0] == "Checking that they are identical"]
    assert hashing[-1][1] == hashing[-1][2] == 5_000_000 and [s[1] for s in hashing] == sorted(s[1] for s in hashing)
