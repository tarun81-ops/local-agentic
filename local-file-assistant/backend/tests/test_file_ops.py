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


