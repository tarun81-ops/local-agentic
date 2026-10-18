from pathlib import Path

from send2trash import send2trash


def trash(path: Path) -> None:
    """Always goes to the Recycle Bin — never a permanent delete."""
    send2trash(str(path))


def erase(path: Path) -> None:
    """Deletes for good, bypassing the Recycle Bin. Only the Organize page's explicit "Delete forever" uses this."""
    from app.core.file_ops import recycle_bin

    recycle_bin.erase(path)


def count_entries(path: Path) -> int:
    """Files and folders inside `path` (0 for a plain file): lets a half-finished folder delete be noticed."""
    import os

    return sum(len(dirs) + len(files) for _, dirs, files in os.walk(path))
