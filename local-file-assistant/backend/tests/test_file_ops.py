from pathlib import Path

import pytest

from app.core.file_ops import file_mover
from app.core.file_ops.paths import PathNotAllowed, inside_roots


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




def test_a_cut_off_log_line_does_not_break_history_or_undo(tmp_path):
    from app.core.file_ops import file_mover

    src = tmp_path / "a.txt"
    src.write_text("x")
    batch = file_mover.new_batch()
    file_mover.move(src, tmp_path / "out" / "a.txt", tmp_path / "log", batch)
    with open(tmp_path / "log" / file_mover.LOG_NAME, "a", encoding="utf-8") as f:
        f.write('{"id": "abc", "batch": "zz", "op": "mo')  # a crash in the middle of a write
    assert [b["moves"] for b in file_mover.history(tmp_path / "log")] == [1]
    assert [r["ok"] for r in file_mover.undo_batch(tmp_path / "log", batch)] == [True]
    assert src.exists() and not (tmp_path / "out").exists()  # the folder the move made is gone again
    assert file_mover.history(tmp_path / "log") == []  # the bad line was dropped on rewrite


def test_undo_keeps_a_folder_that_has_other_things_in_it(tmp_path):
    from app.core.file_ops import file_mover

    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("a"), b.write_text("b")
    batch = file_mover.new_batch()
    file_mover.move(a, tmp_path / "Docs" / "a.txt", tmp_path / "log", batch)
    file_mover.move(b, tmp_path / "Docs" / "b.txt", tmp_path / "log", batch)
    (tmp_path / "Docs" / "mine.txt").write_text("not from the batch")
    assert all(r["ok"] for r in file_mover.undo_batch(tmp_path / "log", batch))
    assert a.exists() and b.exists() and (tmp_path / "Docs" / "mine.txt").exists()
