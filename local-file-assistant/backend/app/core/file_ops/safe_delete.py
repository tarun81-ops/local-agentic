from pathlib import Path

from send2trash import send2trash


def trash(path: Path) -> None:
    """Always goes to the Recycle Bin — never a permanent delete."""
    send2trash(str(path))
