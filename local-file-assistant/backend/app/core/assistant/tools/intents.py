"""Turns the user's own message into a proposed action. Rules, not the model: a proposal can only
come from what the user typed (plus a lookup in their index), never from file text or model
output, so a document can't talk the assistant into running anything."""
import re
from pathlib import Path

from app.config import settings
from app.core.assistant.tools import builtin
from app.db import sqlite_fts

_Q = "\"“”'"
_OPEN = re.compile(r"^\s*(?:please\s+)?(?:open|launch|start)\s+(?:the\s+)?(?:app\s+|file\s+|folder\s+)?(?P<what>.+?)\s*$", re.I)
_NOTE = re.compile(r"^\s*(?:please\s+)?(?:create|make|write|take|save|add)\s+(?:me\s+)?(?:a\s+|an\s+)?(?:new\s+)?note\b(?P<rest>.*)$", re.I | re.S)
_MOVE = re.compile(r"^\s*(?:please\s+)?move\s+(?:the\s+)?(?:file\s+)?(?P<src>.+?)\s+(?:to|into)\s+(?:the\s+)?(?:folder\s+)?(?P<dst>.+?)\s*$", re.I)
_WIN_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_TITLED = re.compile(r"^(?:called|titled|named)\s+(?P<title>.+?)(?:\s+(?:saying|that says|with the text)\s+(?P<b1>.*)|\s*:\s*(?P<b2>.*))?$", re.I | re.S)
_BODY = re.compile(r"^(?:saying|that says|with the text|about|:)\s*:?\s*(?P<body>.+)$", re.I | re.S)


def _find_file(name: str) -> dict | None:
    """An indexed file whose name contains every word of `name`; exact stem first, then shortest."""
    words = name.lower().split()
    conn = sqlite_fts.connect(settings.db_path)
    try:
        files = sqlite_fts.list_files(conn)
    finally:
        conn.close()
    hits = [f for f in files if all(w in Path(f["path"]).name.lower() for w in words)]
    hits.sort(key=lambda f: (Path(f["path"]).stem.lower() != name.lower(), len(Path(f["path"]).name)))
    return hits[0] if hits else None


def _note(rest: str) -> dict:
    rest = rest.strip()
    title = body = ""
    t = _TITLED.match(rest)
    if t:
        title, body = t["title"].strip(_Q + " "), t["b1"] or t["b2"] or ""
    else:
        b = _BODY.match(rest)
        body = b["body"] if b else rest.lstrip(": ")
    title = title or " ".join(body.split()[:6]) or "Note"
    return {"tool": "create_note", "args": {"title": title, "content": body.strip()}}


def from_message(message: str) -> dict | None:
    """{"tool", "args"} for "create a note…", "move X to Y", "open X"; None for anything else,
    including an "open ..." that matches no file or app (so ordinary chat is never hijacked)."""
    m = _NOTE.match(message)
    if m:
        return _note(m["rest"])
    m = _MOVE.match(message)
    if m:
        src, dst = m["src"].strip(_Q + " "), m["dst"].strip(_Q + " ")
        hit = {"path": src, "root": ""} if _WIN_PATH.match(src) else _find_file(src)
        if hit:
            folder = Path(dst) if _WIN_PATH.match(dst) else Path(hit["root"]) / dst
            return {"tool": "move_file", "args": {"path": hit["path"], "to": str(folder / Path(hit["path"]).name)}}
        return None
    m = _OPEN.match(message)
    if m:
        what = m["what"].strip(_Q + " .")
        if what.lower() in builtin.apps():
            return {"tool": "open_app", "args": {"name": what.lower()}}
        if _WIN_PATH.match(what):
            return {"tool": "open_path", "args": {"path": what}}
        hit = _find_file(what)
        if hit:
            return {"tool": "open_path", "args": {"path": hit["path"]}}
    return None
