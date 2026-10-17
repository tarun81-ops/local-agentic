"""Automatic access to the everyday folders (Documents, Desktop, Downloads, Pictures)."""
import pytest

from app.core import known_folders, personalize, prefs
from app.db import sqlite_fts


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    for name in ("Documents", "Desktop", "Downloads"):  # no Pictures: it must be reported as missing
        (tmp_path / name).mkdir()
    (tmp_path / "Documents" / "a.txt").write_text("hello")
    monkeypatch.setattr(known_folders.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(known_folders.sys, "platform", "linux")  # skip the Windows shell lookup
    prefs.set(known_folders.PREF_ENABLED, True)
    prefs.set(known_folders.PREF_DONE, [])
    return tmp_path


def test_startup_offers_only_the_default_folders_once(fake_home, db_paths):
    assert known_folders.startup_keys() == ["documents", "desktop", "downloads"]
    conn = sqlite_fts.connect(db_paths[0])
    added = known_folders.add(known_folders.startup_keys(), conn)
    assert {p.name for p in added} == {"Documents", "Desktop", "Downloads"}
    assert known_folders.startup_keys() == []  # offered already: a folder the user removes stays removed
    conn.close()


def test_added_folders_get_default_exclusions_and_label(fake_home, db_paths):
    conn = sqlite_fts.connect(db_paths[0])
    known_folders.add(["documents"], conn)
    profile = personalize.folder_profile(fake_home / "Documents")
    assert profile["label"] == "Documents"
    assert "node_modules" in profile["exclude_globs"]
    conn.close()


def test_adding_twice_is_a_no_op(fake_home, db_paths):
    conn = sqlite_fts.connect(db_paths[0])
    assert len(known_folders.add(["documents"], conn)) == 1
    assert known_folders.add(["documents"], conn) == []
    conn.close()


def test_missing_and_unknown_folders_are_skipped(fake_home, db_paths):
    conn = sqlite_fts.connect(db_paths[0])
    assert known_folders.add(["pictures", "windows", "../etc"], conn) == []  # only the named folders can ever be added
    conn.close()


def test_switch_off_stops_the_automatic_add(fake_home):
    known_folders.set_enabled(False)
    assert known_folders.startup_keys() == []


def test_listing_reports_state(fake_home, db_paths):
    conn = sqlite_fts.connect(db_paths[0])
    known_folders.add(["documents"], conn)
    rows = {r["key"]: r for r in known_folders.listing(sqlite_fts.root_paths(conn))}
    assert rows["documents"]["indexed"] and not rows["desktop"]["indexed"]
    assert rows["pictures"]["exists"] is False
    conn.close()
