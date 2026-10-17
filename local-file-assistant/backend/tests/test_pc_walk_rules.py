"""Which folders the PC file list walks into. Regression: OneDrive folders (Windows reparse points with a
cloud tag) were treated as links and skipped, so Documents/Desktop in OneDrive were never listed."""
import time

import pytest

from app.config import settings
from app.core import pc_access

REPARSE = 0x400
# Real Windows reparse tags (winnt.h).
IO_REPARSE_TAG_SYMLINK = 0xA000000C
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003  # junctions
IO_REPARSE_TAG_CLOUD = 0x9000001A
IO_REPARSE_TAG_CLOUD_3 = 0x9000301A  # OneDrive uses the CLOUD_n family
IO_REPARSE_TAG_CLOUD_MASK_EXAMPLE = 0x9000F01A


@pytest.mark.parametrize("tag", [IO_REPARSE_TAG_CLOUD, IO_REPARSE_TAG_CLOUD_3, IO_REPARSE_TAG_CLOUD_MASK_EXAMPLE])
def test_onedrive_cloud_folders_are_real_folders(tag):
    assert pc_access.reparse_is_link(REPARSE, tag) is False


@pytest.mark.parametrize("tag", [IO_REPARSE_TAG_SYMLINK, IO_REPARSE_TAG_MOUNT_POINT, 0])
def test_links_junctions_and_unknown_reparse_points_are_not_followed(tag):
    assert pc_access.reparse_is_link(REPARSE, tag) is True


def test_plain_folders_are_not_links():
    assert pc_access.reparse_is_link(0x10, 0) is False  # FILE_ATTRIBUTE_DIRECTORY only


class _Stat:
    def __init__(self, attrs, tag):
        self.st_file_attributes, self.st_reparse_tag = attrs, tag


class _Entry:
    """A Windows DirEntry as os.scandir reports an OneDrive folder or a junction."""

    def __init__(self, path, attrs, tag):
        self.path, self._st = path, _Stat(attrs, tag)

    def is_symlink(self):
        return False

    def stat(self, follow_symlinks=True):
        return self._st


def test_is_link_on_windows_follows_onedrive_but_not_junctions(monkeypatch):
    monkeypatch.setattr(pc_access.sys, "platform", "win32")
    onedrive = _Entry(r"C:\Users\me\OneDrive", REPARSE | 0x10, IO_REPARSE_TAG_CLOUD_3)
    junction = _Entry(r"C:\Users\me\My Documents", REPARSE | 0x10, IO_REPARSE_TAG_MOUNT_POINT)
    plain = _Entry(r"C:\Users\me\Downloads", 0x10, 0)
    assert pc_access._is_link(onedrive) is False
    assert pc_access._is_link(junction) is True
    assert pc_access._is_link(plain) is False
    # DirEntry may not carry the tag; then it is read with lstat.
    no_tag = _Entry(r"C:\Users\me\OneDrive\Documents", REPARSE | 0x10, 0)
    monkeypatch.setattr(pc_access.os, "lstat", lambda p: _Stat(REPARSE | 0x10, IO_REPARSE_TAG_CLOUD))
    assert pc_access._is_link(no_tag) is False


def test_tool_and_runtime_folders_are_skipped_but_personal_ones_listed(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    home = tmp_path / "C" / "Users" / "me"
    files = {
        "OneDrive/Documents/Internship_Offer_Letter.pdf": True,
        "personal documents/marksheet_class10.pdf": True,
        "Downloads/bill.pdf": True,
        ".codex/sessions/notes.md": False,
        ".vscode/extensions/readme.md": False,
        "Python27/Lib/test/readme.txt": False,
        "eSupport/eDriver/manual.pdf": False,
    }
    for rel in files:
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    pc_access.set_config(enabled=True, drives=[str(tmp_path / "C")])
    pc_access.scan([str(tmp_path / "C")])
    conn = pc_access.connect()
    try:
        listed = {r[0] for r in conn.execute("SELECT path FROM catalog")}
    finally:
        conn.close()
    for rel, wanted in files.items():
        assert (str(home / rel) in listed) is wanted, rel
    pc_access.set_config(enabled=False)


def _set_last_scan(conn):
    conn.execute("INSERT INTO meta(k,v) VALUES('last_scan', ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(time.time()),))


def test_changed_walk_rules_trigger_a_rescan_at_the_next_start(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    conn = pc_access.connect()
    with conn:
        _set_last_scan(conn)
        conn.execute("UPDATE meta SET v='1' WHERE k='scan_rules'")  # a catalog built under the old rules
    conn.close()
    conn = pc_access.connect()
    try:
        assert pc_access._meta(conn, "last_scan") is None  # so start_if_stale() scans now
        assert pc_access._meta(conn, "scan_rules") == str(pc_access.SCAN_RULES)
        with conn:
            _set_last_scan(conn)
    finally:
        conn.close()
    conn = pc_access.connect()  # only once: the next scan's timestamp is kept
    try:
        assert pc_access._meta(conn, "last_scan") is not None
    finally:
        conn.close()
