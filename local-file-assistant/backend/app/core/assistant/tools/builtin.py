"""The built-in tools: open a file or folder, open an allow-listed app, write a note, move an
indexed file. Importing this module registers them."""
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

from app.config import settings
from app.core import indexer, prefs
from app.core.assistant.tools.registry import Tool, ToolError, register
from app.core.file_ops import file_mover
from app.core.file_ops.paths import PathNotAllowed, inside_roots, name_problem
from app.core.file_ops.safe_delete import trash
from app.db import sqlite_fts

# Opening these would run a program, not show a document.
BLOCKED_EXT = {".exe", ".bat", ".cmd", ".com", ".msi", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".scr", ".lnk", ".reg", ".hta", ".jar", ".cpl", ".dll", ".msc", ".url"}
DEFAULT_APPS = {"notepad": "notepad.exe", "calculator": "calc.exe", "paint": "mspaint.exe", "file explorer": "explorer.exe"}


def roots() -> list[str]:
    conn = sqlite_fts.connect(settings.db_path)
    try:
        return sqlite_fts.root_paths(conn)
    finally:
        conn.close()


def apps() -> dict[str, str]:
    """App name -> program. Only these can be launched; add more under "allowed_apps" in settings.json."""
    return {k.lower(): v for k, v in {**DEFAULT_APPS, **prefs.get("allowed_apps", {})}.items() if isinstance(v, str)}


def notes_dir() -> Path:
    return Path(prefs.get("notes_dir") or Path.home() / "Documents" / "Assistant Notes")


def _allowed_dirs() -> list[str]:
    home = Path.home()
    dirs = [home / d for d in ("Downloads", "Desktop", "Documents")] + [Path(p) for p in prefs.get("allowed_dirs", [])]
    return [str(d) for d in dirs if d.is_dir()]


def _open(path: str) -> None:
    """The one place files are opened: the OS default handler, never a command line."""
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - validated path, not a command
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


def _launch(exe: str) -> None:
    subprocess.Popen([exe])  # a list, no shell: the program name is from the allow-list


def _reindex(removed: str | Path, added: Path) -> None:
    indexer.remove_file(removed, settings.db_path, settings.vector_db_dir)
    indexer.index_file(added, settings.db_path, settings.vector_db_dir)


# ---------- open_path ----------

def _open_path_validate(a: dict) -> dict:
    if Path(a["path"]).is_symlink():
        raise ToolError("That is a link or shortcut; open the real file instead.")
    try:
        path = inside_roots(a["path"], roots() + _allowed_dirs())
    except PathNotAllowed:
        raise ToolError("I only open files in your indexed folders, Downloads, Desktop or Documents.") from None
    if not path.exists():
        raise ToolError(f"It doesn't exist (any more): {path}")
    if path.is_file() and path.suffix.lower() in BLOCKED_EXT:
        raise ToolError("Programs and scripts are never opened from here.")
    return {"path": str(path)}


register(Tool(
    "open_path", "read", {"path": str}, {}, _open_path_validate,
    lambda a: [f"Open: {a['path']}"],
    lambda a: _open(a["path"]) or {"opened": a["path"]},
))


# ---------- open_app ----------

def _open_app_validate(a: dict) -> dict:
    name = a["name"].strip().lower()
    if name not in apps():
        raise ToolError(f"“{a['name']}” isn't an app I'm allowed to open.")
    return {"name": name}


register(Tool(
    "open_app", "read", {"name": str}, {}, _open_app_validate,
    lambda a: [f"Open the app: {a['name']} ({apps().get(a['name'], '?')})"],
    lambda a: _launch(apps()[a["name"]]) or {"launched": a["name"]},
))


# ---------- create_note ----------

def _note_validate(a: dict) -> dict:
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", a["title"])
    title = " ".join(title.split()).strip(" .")[:80].strip(" .")
    if not title:
        raise ToolError("A note needs a title.")
    problem = name_problem(f"{title}.md")
    if problem:
        raise ToolError(problem)
    return {"title": title, "content": a.get("content", "")}


def _note_run(a: dict) -> dict:
    folder = notes_dir()
    folder.mkdir(parents=True, exist_ok=True)
    text = f"# {a['title']}\n\n{a['content']}\n"
    for n in range(1, 1000):
        path = folder / (f"{a['title']}.md" if n == 1 else f"{a['title']} ({n}).md")
        try:
            with open(path, "x", encoding="utf-8", newline="") as f:  # "x": never overwrites
                f.write(text)
            return {"path": str(path), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        except FileExistsError:
            continue
    raise ToolError("There are too many notes with that title.")


def _note_undo(res: dict) -> dict:
    path = Path(res["path"])
    if not path.is_file():
        raise ToolError("The note is already gone.")
    if hashlib.sha256(path.read_bytes()).hexdigest() != res["sha256"]:
        raise ToolError("The note was edited since, so it was left alone.")
    trash(path)  # Recycle Bin, not a permanent delete
    return {"trashed": str(path)}


def _note_lines(a: dict) -> list[str]:
    preview = a["content"][:300] + ("…" if len(a["content"]) > 300 else "")
    return [f"Create a new note: {a['title']}.md", f"In: {notes_dir()}", *([preview] if preview else [])]


register(Tool("create_note", "reversible", {"title": str}, {"content": str}, _note_validate, _note_lines, _note_run, _note_undo))


# ---------- move_file ----------

def _move_validate(a: dict) -> dict:
    from app.api.routes_organize import _validate  # the organizer's checks: roots, links, Windows names, no overwrite
    from app.models.organize import OrganizeAction

    resolved, problems = _validate([OrganizeAction(op="move", path=a["path"], to=a["to"])], roots())
    if problems:
        raise ToolError(problems[0])
    _, src, dst = resolved[0]
    return {"path": str(src), "to": str(dst)}


def _move_run(a: dict) -> dict:
    src, dst = Path(a["path"]), Path(a["to"])
    batch = file_mover.new_batch()
    file_mover.move(src, dst, settings.data_dir, batch)
    _reindex(src, dst)
    return {"batch": batch, "from": str(src), "to": str(dst)}


def _move_undo(res: dict) -> dict:
    [r] = file_mover.undo_batch(settings.data_dir, res["batch"])
    if not r["ok"]:
        raise ToolError(r["error"])
    _reindex(r["from"], Path(r["to"]))
    return {"restored": r["to"]}


register(Tool(
    "move_file", "reversible", {"path": str, "to": str}, {}, _move_validate,
    lambda a: [f"Move: {a['path']}", f"To: {a['to']}"],
    _move_run, _move_undo,
))
