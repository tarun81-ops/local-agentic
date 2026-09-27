import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

LOG_NAME = "undo_log.jsonl"


def move(from_path: Path, to_path: Path, log_dir: Path) -> None:
    """A trash delete is already reversible via the Recycle Bin, but shutil.move isn't —
    log each move so it can be undone."""
    to_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(from_path), str(to_path))
    entry = {"from": str(from_path), "to": str(to_path), "at": datetime.now(timezone.utc).isoformat()}
    with open(log_dir / LOG_NAME, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def undo_last(log_dir: Path) -> dict | None:
    log_file = log_dir / LOG_NAME
    if not log_file.exists():
        return None
    lines = log_file.read_text(encoding="utf-8").splitlines()
    if not lines:
        return None
    last = json.loads(lines[-1])
    shutil.move(last["to"], last["from"])
    log_file.write_text("\n".join(lines[:-1]) + ("\n" if len(lines) > 1 else ""), encoding="utf-8")
    return last


def _demo():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / "a.txt"
        src.write_text("hello")
        dest = tmp / "sub" / "a.txt"

        move(src, dest, tmp)
        assert dest.exists() and not src.exists()

        undone = undo_last(tmp)
        assert undone["from"] == str(src)
        assert src.exists() and not dest.exists()
        assert undo_last(tmp) is None  # log now empty

    print("file_mover self-check OK")


if __name__ == "__main__":
    _demo()
