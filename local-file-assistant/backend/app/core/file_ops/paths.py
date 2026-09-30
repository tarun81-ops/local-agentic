import os
import re
from pathlib import Path


class PathNotAllowed(ValueError):
    pass


# Windows limits (the app's target). Checked on every platform so a plan that works in the
# tests also works on the laptop.
MAX_PATH_CHARS = 259  # MAX_PATH (260) minus the terminating NUL; long-path support is off by default
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def inside_roots(path: str | Path, roots: list[str]) -> Path:
    """Resolves path (following symlinks, junctions and ..) and requires it to sit inside one
    of the indexed folders. Anything else — system folders, other drives — is refused,
    whatever the client sends."""
    resolved = Path(path).resolve(strict=False)
    for root in roots:
        if resolved.is_relative_to(Path(root).resolve(strict=False)):
            return resolved
    raise PathNotAllowed(f"outside the indexed folders: {path}")


def name_problem(part: str) -> str | None:
    """Why a single file/folder name can't be created on Windows, or None if it's fine."""
    if not part or part in (".", ".."):
        return "empty name"
    if _BAD_CHARS.search(part):
        return f'"{part}" contains a character Windows doesn\'t allow (< > : " | ? *)'
    if part.endswith((" ", ".")):
        return f'"{part}" ends with a space or dot'
    if part.split(".")[0].strip().lower() in _RESERVED:
        return f'"{part}" is a reserved Windows name'
    return None


def path_problem(path: Path) -> str | None:
    """Checks a destination path for Windows: every name valid, total length under MAX_PATH."""
    if len(str(path)) > MAX_PATH_CHARS:
        return f"path is too long for Windows ({len(str(path))} characters, max {MAX_PATH_CHARS}): {path}"
    for part in path.parts[1:]:  # parts[0] is the drive / anchor
        problem = name_problem(part)
        if problem:
            return problem
    return None


def same_file_key(path: Path) -> str:
    """Windows paths are case-insensitive: Report.pdf and report.PDF are the same file."""
    return os.path.normcase(str(path)).lower()
