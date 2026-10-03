"""Finds the right document for a form field ("Upload your resume (PDF)"): files whose name or
content matches the field's label, filtered by what the field accepts. Nothing here logs file
names or contents."""
import fnmatch
import logging
import mimetypes
import re
from pathlib import Path

from app.config import settings
from app.core import prefs
from app.core.search.hybrid import hybrid_search
from app.db import sqlite_fts

log = logging.getLogger(__name__)

# Words that appear on upload fields but say nothing about which file is wanted.
_NOISE = {"upload", "your", "the", "and", "for", "file", "files", "document", "documents", "attach", "attachment", "choose", "select", "here", "please", "browse", "drop", "copy", "scan", "scanned", "optional", "required", "max", "size", "format", "with", "from"}


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower()) if w not in _NOISE]


def accepts(path: str, accept: str | list[str] | None) -> bool:
    """The HTML `accept` rule: extensions (".pdf"), MIME types ("application/pdf") or wildcards ("image/*")."""
    tokens = [t.strip().lower() for t in (accept.split(",") if isinstance(accept, str) else accept or []) if t.strip()]
    if not tokens:
        return True
    suffix = Path(path).suffix.lower()
    mime = (mimetypes.guess_type(path)[0] or "").lower()
    for t in tokens:
        if t.startswith(".") and suffix == t:
            return True
        if t.endswith("/*") and mime.startswith(t[:-1]):
            return True
        if t == mime:
            return True
    return False


def is_sensitive(path: str) -> bool:
    """Matches one of the user's "ask every time" patterns (for example "*/id/*"): shown, but not
    attachable until the user confirms that file."""
    norm = path.replace("\\", "/").lower()
    return any(fnmatch.fnmatch(norm, g.replace("\\", "/").lower()) for g in prefs.get("ask_each_time_globs", []) if isinstance(g, str))


def suggest(label: str, page_title: str = "", accept: str | list[str] | None = None, limit: int = 8) -> list[dict]:
    """Best files first: name matches (most label words, newest), then content matches in search order."""
    terms = words(label) or words(page_title)
    if not terms:
        return []
    conn = sqlite_fts.connect(settings.db_path)
    try:
        files = {f["path"]: f for f in sqlite_fts.list_files(conn)}
    finally:
        conn.close()

    by_name = []
    for path, f in files.items():
        stem = Path(path).stem.lower()
        hits = sum(t in stem for t in terms)
        if hits:
            by_name.append((hits, f["mtime"], path))
    by_name.sort(reverse=True)

    try:
        content = hybrid_search(" ".join(terms), limit=40)["results"]
    except Exception:  # name matches are still useful when the index search fails
        log.warning("form suggestion: content search skipped")
        content = []

    ranked = [(p, "name", "") for _, _, p in by_name] + [(r["path"], r["match"], r["snippet"]) for r in content]
    out, seen = [], set()
    for path, match, snippet in ranked:
        f = files.get(path)
        if path in seen or f is None or not accepts(path, accept) or not Path(path).is_file():
            continue
        seen.add(path)
        out.append({"path": path, "name": Path(path).name, "size": f["size"], "mtime": f["mtime"], "match": match, "snippet": snippet, "sensitive": is_sensitive(path)})
        if len(out) >= limit:
            break
    return out
