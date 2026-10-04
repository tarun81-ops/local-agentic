from pathlib import Path

import pytest

from app.core.file_ops import file_mover
from app.core.file_ops.paths import PathNotAllowed, inside_roots
from app.core.llm.organizer import clean_folder, propose_plan


def test_move_and_undo_batch(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("a")
    b.write_text("b")
    batch = file_mover.new_batch()
    file_mover.move(a, tmp_path / "x" / "a.txt", tmp_path, batch)
    file_mover.move(b, tmp_path / "y" / "b.txt", tmp_path, batch)
    assert file_mover.history(tmp_path)[0]["moves"] == 2

    results = file_mover.undo_batch(tmp_path, batch)
    assert all(r["ok"] for r in results)
    assert a.exists() and b.exists()
    assert file_mover.history(tmp_path) == []


def test_move_never_overwrites_and_leaves_no_log(tmp_path):
    src, dst = tmp_path / "a.txt", tmp_path / "b.txt"
    src.write_text("new")
    dst.write_text("keep me")
    with pytest.raises(FileExistsError):
        file_mover.move(src, dst, tmp_path, file_mover.new_batch())
    assert dst.read_text() == "keep me" and src.exists()
    assert file_mover.history(tmp_path) == []


def test_inside_roots_blocks_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    assert inside_roots(root / "a" / "b.pdf", [str(root)]) == (root / "a" / "b.pdf").resolve()
    with pytest.raises(PathNotAllowed):
        inside_roots(root / ".." / "secret.txt", [str(root)])
    with pytest.raises(PathNotAllowed):
        inside_roots("/etc/passwd", [str(root)])


@pytest.mark.parametrize("bad", ["", "/abs", "C:/Windows", "../up", "a/../../b", "."])
def test_clean_folder_rejects_unsafe(bad):
    assert clean_folder(bad) is None


def test_plan_finds_duplicates_and_junk_without_the_model():
    files = [
        {"path": "/r/a.pdf", "size": 1, "hint": ""},
        {"path": "/r/copy of a.pdf", "size": 1, "hint": ""},
        {"path": "/r/notes.txt.bak", "size": 1, "hint": ""},
        {"path": "/r/invoice.pdf", "size": 1, "hint": "Invoice total"},
    ]
    dups = [[{"path": "/r/a.pdf", "mtime": 1}, {"path": "/r/copy of a.pdf", "mtime": 2}]]

    def fake_model(candidates, model):
        assert [c["path"] for c in candidates] == ["/r/a.pdf", "/r/invoice.pdf"]
        return [
            {"path": "/r/invoice.pdf", "folder": "Finance/Invoices"},
            {"path": "/r/invented.pdf", "folder": "X"},
            {"path": "/r/a.pdf", "folder": "../../escape"},
        ]

    plan = propose_plan("/r", files, dups, ask_model=fake_model)
    ops = {(a.op, a.path, a.to) for a in plan.actions}
    assert ("delete", "/r/copy of a.pdf", None) in ops
    assert ("delete", "/r/notes.txt.bak", None) in ops
    assert ("move", "/r/invoice.pdf", str(Path("/r") / "Finance" / "Invoices" / "invoice.pdf")) in ops  # destinations use the OS separator
    assert plan.dropped_invalid == 2
