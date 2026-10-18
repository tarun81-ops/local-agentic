"""Organize -> permanent delete, and the Recycle Bin tab (list, restore, delete for good, empty).
The bin is simulated with $I/$R pairs in a temp folder, the way Windows lays them out."""
import pathlib
import struct
from datetime import datetime, timedelta, timezone

import pytest

from app.api import routes_organize
from app.core.file_ops import recycle_bin

from .test_move_and_delete import AUTH, _apply, _make, client, pc  # noqa: F401  (fixtures)


def _filetime(dt: datetime) -> int:
    return int((dt - recycle_bin._EPOCH).total_seconds() * 10_000_000)


def _put(bin_dir: pathlib.Path, key: str, original: str, data=b"data", days_ago=0, version=2, folder=False):
    """One deleted item: $I<key><ext> (record) and $R<key><ext> (the file or folder)."""
    ext = pathlib.PureWindowsPath(original).suffix
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    path_bytes = (original + "\0").encode("utf-16-le")
    if version == 2:
        rec = struct.pack("<qqqi", 2, len(data), _filetime(when), len(original) + 1) + path_bytes
    else:
        rec = struct.pack("<qqq", 1, len(data), _filetime(when)) + path_bytes.ljust(520, b"\0")
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / f"$I{key}{ext}").write_bytes(rec)
    r = bin_dir / f"$R{key}{ext}"
    if folder:
        r.mkdir()
        (r / "inner.txt").write_bytes(data)
    else:
        r.write_bytes(data)
    return r


@pytest.fixture
def bin_dir(tmp_path, monkeypatch):
    d = tmp_path / "bin" / "S-1-5-21-1"
    d.mkdir(parents=True)
    monkeypatch.setattr(recycle_bin, "bin_dirs", lambda: [d])
    return d


def test_list_reads_both_record_versions_newest_first(bin_dir):
    _put(bin_dir, "AAAAAA", r"C:\Users\me\Downloads\old.pdf", b"12345", days_ago=40)
    _put(bin_dir, "BBBBBB", r"C:\Users\me\Documents\Résumé.docx", b"1", days_ago=1, version=1)
    _put(bin_dir, "CCCCCC", r"C:\Users\me\Desktop\Project", b"xy", days_ago=5, folder=True)
    (bin_dir / "$IDDDDDD.txt").write_bytes(b"junk")  # a record with no data and an unreadable header: skipped
    items = recycle_bin.list_items()
    assert [i["name"] for i in items] == ["Résumé.docx", "Project", "old.pdf"]
    old = items[2]
    assert (old["folder"], old["size"], old["is_dir"]) == (r"C:\Users\me\Downloads", 5, False)
    assert items[1]["is_dir"] and datetime.fromisoformat(old["deleted_at"]) < datetime.fromisoformat(items[1]["deleted_at"])


def test_purge_deletes_data_and_record_but_only_what_the_bin_lists(bin_dir, tmp_path):
    r = _put(bin_dir, "AAAAAA", r"C:\x\a.txt")
    f = _put(bin_dir, "CCCCCC", r"C:\x\Folder", folder=True)
    (f / "inner.txt").chmod(0o444)  # read-only contents must not stop it
    bystander = _make(tmp_path / "keep.txt")
    ids = [i["id"] for i in recycle_bin.list_items()]
    out = recycle_bin.purge(ids + [str(bystander)])  # a path that is not a bin record is refused, never deleted
    assert [o["ok"] for o in out] == [True, True, False] and "no longer in the Recycle Bin" in out[2]["error"]
    assert not r.exists() and not f.exists() and not list(bin_dir.iterdir())
    assert bystander.exists()


def test_restore_puts_it_back_and_never_overwrites(bin_dir, tmp_path):
    dest = tmp_path / "back" / "a.txt"
    _put(bin_dir, "AAAAAA", str(dest), b"mine")
    ids = [i["id"] for i in recycle_bin.list_items()]
    assert recycle_bin.restore(ids)[0]["to"] == str(dest) and dest.read_bytes() == b"mine"  # parent folder recreated
    _put(bin_dir, "BBBBBB", str(dest), b"newer")
    res = recycle_bin.restore([i["id"] for i in recycle_bin.list_items()])[0]
    assert res["to"] == str(tmp_path / "back" / "a (restored).txt") and dest.read_bytes() == b"mine"
    assert not list(bin_dir.iterdir())


def test_api_lists_purges_restores_and_empties(client, bin_dir, tmp_path):
    _put(bin_dir, "AAAAAA", r"C:\x\a.txt", b"12345")
    _put(bin_dir, "BBBBBB", str(tmp_path / "back" / "b.txt"), b"bb")
    _put(bin_dir, "CCCCCC", r"C:\x\c.txt", b"c")
    listing = client.get("/organize/bin", headers=AUTH).json()
    assert listing["count"] == 3 and listing["bytes"] == 8
    by_name = {i["name"]: i["id"] for i in listing["items"]}
    r = client.post("/organize/bin/restore", json={"ids": [by_name["b.txt"]]}, headers=AUTH).json()
    assert r["count"] == 2 and (tmp_path / "back" / "b.txt").read_bytes() == b"bb"
    r = client.post("/organize/bin/purge", json={"ids": [by_name["a.txt"]]}, headers=AUTH).json()
    assert [x["ok"] for x in r["results"]] == [True] and r["count"] == 1
    hist = client.get("/organize/history", headers=AUTH).json()["batches"]
    assert hist[0]["purged"] == 1 and hist[0]["undoable"] == 0  # logged, and not undoable
    assert client.post("/organize/bin/purge", json={"ids": []}, headers=AUTH).status_code == 400
    r = client.post("/organize/bin/empty", headers=AUTH).json()
    assert r["count"] == 0 and not list(bin_dir.iterdir())


def test_delete_forever_skips_the_bin_logs_it_and_keeps_the_guards(client, pc):
    home, trashed = pc["home"], pc["trashed"]
    dl, docs = home / "Downloads", home / "Documents"
    bad = _apply(client, {"op": "delete_forever", "path": str(docs / "Project")},
                 {"op": "delete_forever", "path": str(dl / "passwords.txt")})  # credential-like: refused, nothing runs
    assert bad.status_code == 400 and (docs / "Project").exists()
    assert _apply(client, {"op": "delete_forever", "path": str(home)}).status_code == 400  # main folder: never
    assert _apply(client, {"op": "delete_forever", "path": str(pc["drive"])}).status_code == 400  # a whole drive: never
    ro = _make(dl / "ro.txt")
    ro.chmod(0o444)
    res = _apply(client, {"op": "delete_forever", "path": str(docs / "Project")},
                 {"op": "delete_forever", "path": str(ro)}).json()
    assert all(r["ok"] and r["op"] == "delete_forever" for r in res["results"])
    assert not (docs / "Project").exists() and not ro.exists()
    assert trashed == []  # never touched the Recycle Bin
    batch = client.get("/organize/history", headers=AUTH).json()["batches"][0]
    assert batch["purged"] == 2 and batch["undoable"] == 0


def test_a_huge_bin_shows_the_newest_rows_but_counts_and_empties_everything(client, bin_dir, monkeypatch):
    monkeypatch.setattr(routes_organize, "BIN_SHOWN", 2)
    for n in range(5):
        _put(bin_dir, f"AAAAA{n}", rf"C:\x\f{n}.txt", b"1", days_ago=n)
    r = client.get("/organize/bin", headers=AUTH).json()
    assert [i["name"] for i in r["items"]] == ["f0.txt", "f1.txt"] and r["count"] == 5 and r["bytes"] == 5 and r["truncated"]
    assert client.post("/organize/bin/empty", headers=AUTH).json()["count"] == 0 and not list(bin_dir.iterdir())
