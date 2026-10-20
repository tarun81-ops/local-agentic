"""Document photos: finding a marksheet that is saved as IMG-20250626-WA0122.jpg.

In names-only mode a photo is found by its file name alone, and phone photos of documents have names that say
nothing. So, once per photo, a background pass runs OCR and keeps a small "card": the first HEAD_CHARS characters
of the text (the heading and the name usually come first: "CENTRAL BOARD OF SECONDARY EDUCATION ... MARKS
STATEMENT ... Name of Candidate ..."). Photos with almost no text (people, places) are kept as "not a document".
A card is a few hundred bytes, so ~1,000 photos take well under 1 MB; the full text is never stored. The answer
still comes from reading the photo live (file_agent.read_chunks).

Gentle like the content reader: one photo at a time, pausing while chat is busy, on low RAM and on battery
(pc_content.pause_reason). The user's own folders and phone "Documents" folders go first, newest first.
Edited or replaced photos are read again (size + modified time). Switch off with DOC_PHOTO_SCAN=false.

Cards live in catalog.db: table photo_cards and the FTS table photo_cards_fts."""
import logging
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from app.config import settings
from app.core import doc_kinds, pc_access

log = logging.getLogger(__name__)

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
HEAD_CHARS = 400
MIN_TEXT_CHARS = 40  # less than this after OCR: a photo, not a document
MIN_BYTES = 20 * 1024  # icons, stickers, thumbnails
MAX_BYTES = 20 * 1024 * 1024
BATCH = 25
IDLE_SLEEP_S = 300
PAUSED_SLEEP_S = 30
# Folder words that make a photo more likely to be a document: read these first.
_DOC_FOLDERS = ("documents", "document", "docs", "scan", "scans", "certificate", "marksheet", "result", "downloads", "desktop", "camscanner")

def enabled() -> bool:
    return bool(getattr(settings, "doc_photo_scan", True)) and pc_access.enabled()


def _conn() -> sqlite3.Connection:
    conn = pc_access.connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS photo_cards(path TEXT PRIMARY KEY, size INTEGER, mtime REAL, is_doc INTEGER, head TEXT, at REAL);
        CREATE VIRTUAL TABLE IF NOT EXISTS photo_cards_fts USING fts5(path UNINDEXED, head, tokenize="unicode61 remove_diacritics 2");
        """
    )
    return conn


def card_text(text: str) -> tuple[bool, str]:
    """(is a document, the text kept for search): kind words first, then the start of the OCR text."""
    flat = " ".join((text or "").split())
    if len(re.sub(r"[^A-Za-z0-9]", "", flat)) < MIN_TEXT_CHARS:
        return False, ""
    tags = doc_kinds.kinds(flat) + [doc_kinds.CLASS_WORDS[n] for n in sorted(doc_kinds.classes_in(flat), reverse=True)]
    return True, (" ".join(tags) + " | " if tags else "") + flat[:HEAD_CHARS]


def _ocr(path: Path) -> str:
    from app.core.parsers import image_parser

    return image_parser.ocr(path)


def _home_first(path: str) -> int:
    low = path.lower()
    score = 0
    if low.startswith(str(Path.home()).lower()):
        score -= 1
    if any(f"\\{w}\\" in low or f"/{w}/" in low or f" {w}\\" in low or f" {w}/" in low for w in _DOC_FOLDERS):
        score -= 2
    return score


def pending(conn: sqlite3.Connection, limit: int = BATCH) -> list[sqlite3.Row]:
    """Photos in the catalog without an up-to-date card, most likely documents first."""
    marks = ",".join("?" * len(IMAGE_EXTS))
    rows = conn.execute(
        f"SELECT c.path, c.size, c.mtime FROM catalog c LEFT JOIN photo_cards p ON p.path = c.path "
        f"WHERE c.ext IN ({marks}) AND c.size BETWEEN ? AND ? AND (p.path IS NULL OR p.size <> c.size OR abs(p.mtime - c.mtime) > 1)",
        (*IMAGE_EXTS, MIN_BYTES, MAX_BYTES),
    ).fetchall()
    rows = [r for r in rows if not pc_access.is_sensitive(r["path"])]
    rows.sort(key=lambda r: (_home_first(r["path"]), -(r["mtime"] or 0)))
    return rows[:limit]


def read_one(conn: sqlite3.Connection, row) -> bool:
    """OCR one photo and save its card. True if it is a document."""
    path = row["path"]
    ok, _why = pc_access.can_read(path)
    text = ""
    if ok and os.path.isfile(path):
        try:
            text = _ocr(Path(path))
        except Exception as exc:  # a broken image gets an empty card, so it is not retried every round
            log.info("photo card: OCR failed for %s: %s", path, exc)
    is_doc, head = card_text(text)
    conn.execute("INSERT OR REPLACE INTO photo_cards(path, size, mtime, is_doc, head, at) VALUES (?,?,?,?,?,?)",
                 (path, row["size"], row["mtime"], int(is_doc), head, time.time()))
    conn.execute("DELETE FROM photo_cards_fts WHERE path = ?", (path,))
    if is_doc:
        conn.execute("INSERT INTO photo_cards_fts(path, head) VALUES (?,?)", (path, head))
    conn.commit()
    return is_doc


def run_once(limit: int = BATCH, stop: threading.Event | None = None, should_pause=None) -> int:
    """Reads up to `limit` photos. Returns how many were read."""
    from app.core.parsers import image_parser

    if not image_parser.available():
        return 0
    conn = _conn()
    try:
        done = 0
        for row in pending(conn, limit):
            if (stop is not None and stop.is_set()) or (should_pause and should_pause()):
                break  # chat started, RAM ran low, ...: stop between photos, never in the middle of one
            read_one(conn, row)
            done += 1
        return done
    finally:
        conn.close()


def progress() -> dict:
    """{"photos": all photos in the catalog, "read": with a card, "documents": cards that are documents}."""
    conn = _conn()
    try:
        marks = ",".join("?" * len(IMAGE_EXTS))
        total = conn.execute(f"SELECT count(*) FROM catalog WHERE ext IN ({marks}) AND size BETWEEN ? AND ?", (*IMAGE_EXTS, MIN_BYTES, MAX_BYTES)).fetchone()[0]
        read, docs = conn.execute("SELECT count(*), coalesce(sum(is_doc), 0) FROM photo_cards").fetchone()
        return {"photos": total, "read": min(read, total), "documents": docs}
    finally:
        conn.close()


def search(terms: list[str], limit: int = 8) -> list[dict]:
    """Document photos whose text matches the search words (any word; most matches first), in the same shape as
    pc_access.find() rows."""
    from datetime import datetime, timezone

    groups = []
    for t in terms:
        t = re.sub(r"[^\w]", "", t.lower())
        if len(t) < 2:
            continue
        alts = [t, *pc_access.SYNONYMS.get(t, [])]
        groups.append("(" + " OR ".join(f'"{a}"*' for a in dict.fromkeys(alts) if a) + ")")
    if not groups:
        return []
    try:
        conn = _conn()
    except sqlite3.Error:
        return []
    try:
        rows = []
        for joiner in (" AND ", " OR "):
            rows = conn.execute(
                "SELECT f.path, c.name, c.folder, c.ext, c.size, c.mtime FROM photo_cards_fts f JOIN catalog c ON c.path = f.path "
                "WHERE photo_cards_fts MATCH ? ORDER BY bm25(photo_cards_fts), c.mtime DESC LIMIT ?",
                (joiner.join(groups), limit * 2),
            ).fetchall()
            if rows:
                break
    except sqlite3.Error as exc:
        log.info("photo card search failed: %s", exc)
        return []
    finally:
        conn.close()
    out = []
    for r in rows:
        if pc_access.is_sensitive(r["path"]) or not os.path.isfile(r["path"]):
            continue
        out.append({"path": r["path"], "name": r["name"], "folder": r["folder"], "ext": r["ext"], "size": r["size"],
                    "modified": datetime.fromtimestamp(r["mtime"] or 0, timezone.utc).strftime("%Y-%m-%d"), "via": "photo"})
        if len(out) >= limit:
            break
    return out


class PhotoWorker:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.state = {"paused": "", "read": 0}

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="doc-photos", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        from app.core import pc_content

        while not self._stop.is_set():
            if not enabled():
                self.state["paused"] = "off"
                self._stop.wait(IDLE_SLEEP_S)
                continue
            reason = pc_content.pause_reason()
            if reason:
                self.state["paused"] = reason
                self._stop.wait(PAUSED_SLEEP_S)
                continue
            self.state["paused"] = ""
            try:
                did = run_once(BATCH, self._stop, pc_content.pause_reason)
                self.state["read"] += did
            except Exception:
                log.exception("document photo round failed")
                did = 0
            if not did:
                self._stop.wait(IDLE_SLEEP_S)


worker = PhotoWorker()
