"""Whole-PC access, tier 2: reading file CONTENTS in the background.

Each enabled drive is registered as a hidden root in the main index (index.db), so search, citations and
answers work on those files exactly as on a folder the user added by hand. Hidden roots are not listed on the
Index page, not watched (a recursive watch on a whole drive would flood the app) and never walked with rglob.
Instead this worker takes file paths from the name catalog (pc_access) and indexes them a few at a time:

  * most useful first: the user's own folders, then documents before images, newest first;
  * gently: pauses on low RAM, on battery, and while the chat model was just used, one file at a time;
  * only files pc_access.can_read() allows (enabled drive, no links, no credential files, no system folders);
  * changes are noticed by the periodic catalog rescan: new/changed files are re-read, vanished ones are
    dropped from the index.

State lives in catalog.db (table content_done), so a restart carries on where it stopped."""
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

from app.config import settings
from app.core import pc_access, ram

log = logging.getLogger(__name__)

# One batch and one forget at a time: a forget during a batch would leave rows under a root that no longer exists.
_ROUND_LOCK = threading.Lock()

MAX_BYTES = 50 * 1024 * 1024  # larger files are skipped (listed by name only)
BATCH = 40
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".heic", ".tif", ".tiff"}
CODE_EXTS = {".py", ".java", ".c", ".cpp", ".js", ".ts", ".ipynb", ".html", ".htm"}  # catalogued, but not bulk-read
PAUSE_AFTER_CHAT_S = 20
IDLE_SLEEP_S = 60
RETRY_PARTIAL_S = 15 * 60  # files read by keyword only (embedder was down) get their vectors later
PAUSED_SLEEP_S = 30


def _conn() -> sqlite3.Connection:
    conn = pc_access.connect()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS content_done(path TEXT PRIMARY KEY, size INTEGER, mtime REAL, state TEXT NOT NULL, "
        "err TEXT, at REAL)"
    )
    return conn


def wanted_exts(read_images: bool | None = None) -> list[str]:
    """Extensions the worker reads: what the parsers support, minus media/code, plus images only if asked."""
    from app.core.parsers import PARSERS

    if read_images is None:
        read_images = pc_access.config()["read_images"]
    out = []
    for ext in PARSERS:
        if ext not in pc_access.PERSONAL_EXTS or ext in CODE_EXTS:
            continue
        if ext in IMAGE_EXTS and not read_images:
            continue
        out.append(ext)
    return sorted(out)


def _in_list(n: int) -> str:
    return ",".join("?" * n)


# ---------- roots ----------

def sync_roots() -> None:
    """Hidden roots = the enabled drives. Drives no longer covered are forgotten (contents dropped)."""
    from app.db import sqlite_fts

    cfg = pc_access.config()
    if not cfg["enabled"]:
        return  # switched off: keep what was read, search filters it out
    want = {str(Path(d).resolve()) for d in cfg["drives"]}
    conn = sqlite_fts.connect(settings.db_path)
    try:
        have = set(sqlite_fts.hidden_roots(conn))
        for r in want - have:
            sqlite_fts.add_root(conn, Path(r), hidden=True)
        gone = have - want
    finally:
        conn.close()
    for r in gone:
        forget(r)


def forget(root: str | None = None) -> int:
    """Drops everything read from the PC drives (or one drive) from the index. Files on disk are untouched."""
    with _ROUND_LOCK:
        return _forget_locked(root)


def _forget_locked(root: str | None) -> int:
    from app.core import indexer
    from app.db import sqlite_fts

    conn = sqlite_fts.connect(settings.db_path)
    try:
        roots = [root] if root else sqlite_fts.hidden_roots(conn)
    finally:
        conn.close()
    n = 0
    for r in roots:
        n += indexer.remove_root(r, settings.db_path, settings.vector_db_dir)
    c = _conn()
    try:
        c.execute("CREATE TABLE IF NOT EXISTS parse_cache(path TEXT PRIMARY KEY, size INTEGER, mtime REAL, chunks TEXT)")
        if root:
            c.execute("DELETE FROM content_done WHERE path LIKE ? ESCAPE '\\'", (_like_prefix(root),))
            c.execute("DELETE FROM parse_cache WHERE path LIKE ? ESCAPE '\\'", (_like_prefix(root),))
        else:
            c.execute("DELETE FROM content_done")
            c.execute("DELETE FROM parse_cache")
        c.commit()
    finally:
        c.close()
    return n


# ---------- queue ----------

def _like_prefix(path: str) -> str:
    """LIKE pattern for 'everything under path' with %, _ and \\ in the path taken literally (use ESCAPE '\\')."""
    esc = os.path.normcase(str(path)).rstrip("\\/").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return esc + ("\\\\" if os.sep == "\\" else os.sep) + "%"


def _home_like() -> str:
    return _like_prefix(str(Path.home()))


def next_batch(conn: sqlite3.Connection, limit: int = BATCH) -> list[sqlite3.Row]:
    """Catalog files not yet read (or changed since), most useful first."""
    exts = wanted_exts()
    if not exts:
        return []
    imgs = ",".join(f"'{e}'" for e in sorted(IMAGE_EXTS))
    sql = (
        "SELECT c.path, c.size, c.mtime, c.ext FROM catalog c LEFT JOIN content_done d ON d.path = c.path "
        f"WHERE c.ext IN ({_in_list(len(exts))}) AND c.size <= ? AND c.size > 0 "
        "AND (d.path IS NULL OR d.size <> c.size OR ABS(d.mtime - c.mtime) > 0.5 OR (d.state = 'partial' AND d.at < ?)) "
        f"ORDER BY (c.path LIKE ? ESCAPE '\\') DESC, (c.ext IN ({imgs})) ASC, c.mtime DESC LIMIT ?"
    )
    return conn.execute(sql, (*exts, MAX_BYTES, time.time() - RETRY_PARTIAL_S, _home_like(), limit)).fetchall()


def progress(conn: sqlite3.Connection | None = None) -> dict:
    """{eligible, done, failed, pending} over the catalog."""
    own = conn is None
    conn = conn or _conn()
    try:
        exts = wanted_exts()
        if not exts:
            return {"eligible": 0, "done": 0, "failed": 0, "pending": 0}
        q = (
            "SELECT COUNT(*), COALESCE(SUM(d.state IN ('indexed','skipped','ignored','partial')),0), COALESCE(SUM(d.state='failed'),0) "
            "FROM catalog c LEFT JOIN content_done d ON d.path=c.path AND d.size=c.size AND ABS(d.mtime-c.mtime)<=0.5 "
            f"WHERE c.ext IN ({_in_list(len(exts))}) AND c.size<=? AND c.size>0"
        )
        total, done, failed = conn.execute(q, (*exts, MAX_BYTES)).fetchone()
        return {"eligible": total, "done": int(done), "failed": int(failed), "pending": total - int(done) - int(failed)}
    finally:
        if own:
            conn.close()


def sweep_orphans(conn: sqlite3.Connection, crawler_running: bool, limit: int = 500) -> int:
    """Files read earlier that the catalog no longer lists (deleted/moved) are dropped from the index.
    Skipped while a catalog scan runs or the catalog is empty: those states look like 'everything vanished'."""
    if crawler_running:
        return 0
    if conn.execute("SELECT COUNT(*) FROM catalog").fetchone()[0] == 0:
        return 0
    rows = conn.execute(
        "SELECT d.path FROM content_done d LEFT JOIN catalog c ON c.path = d.path WHERE c.path IS NULL LIMIT ?", (limit,)
    ).fetchall()
    if not rows:
        return 0
    from app.core import indexer

    n = 0
    for (path,) in rows:
        try:
            if not Path(path).exists():
                indexer.remove_file(path, settings.db_path, settings.vector_db_dir)
                n += 1
        except OSError:
            continue
        conn.execute("DELETE FROM content_done WHERE path = ?", (path,))
    conn.commit()
    return n


# ---------- pausing ----------

def pause_reason() -> str:
    """Why the worker should wait right now, or ''."""
    try:
        from app.core.llm import idle

        if idle.recently_used(PAUSE_AFTER_CHAT_S):
            return "chat is busy"
    except Exception:
        pass
    try:
        if ram.is_low():
            return "low memory"
    except Exception:
        pass
    try:
        import psutil

        b = psutil.sensors_battery()
        if b is not None and not b.power_plugged:
            return "on battery"
    except Exception:
        pass
    return ""


# ---------- worker ----------

def _inside(path: Path, root: str) -> bool:
    p, r = os.path.normcase(str(path)), os.path.normcase(str(root)).rstrip("\\/") + os.sep
    return p.startswith(r)


def _drive_root_for(path: Path, drives: list[str]) -> Path | None:
    best = None
    for d in drives:
        base = Path(d).resolve()
        if _inside(path, str(base)) and (best is None or len(str(base)) > len(str(best))):
            best = base
    return best


class ContentWorker:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._state = {"paused": "", "current": "", "indexed": 0, "failed": 0}
        self._counts_at = 0.0
        self._counts: dict = {}

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="pc-content", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def state(self) -> dict:
        now = time.monotonic()
        if now - self._counts_at > 8:  # counting a big catalog on every poll would be wasteful
            try:
                self._counts = progress()
            except sqlite3.Error:
                pass
            self._counts_at = now
        cfg = pc_access.config()
        return {**self._state, "running": self.alive() and cfg["enabled"] and cfg["read_content"], **self._counts,
                "read_content": cfg["read_content"], "read_images": cfg["read_images"]}

    def _loop(self) -> None:
        log.info("PC content worker started")
        try:
            while not self._stop.is_set():
                cfg = pc_access.config()
                if not (cfg["enabled"] and cfg["read_content"] and cfg["drives"]):
                    self._state.update(paused="off", current="")
                    self._stop.wait(IDLE_SLEEP_S)
                    continue
                reason = pause_reason()
                if reason:
                    self._state.update(paused=reason, current="")
                    self._stop.wait(PAUSED_SLEEP_S)
                    continue
                self._state["paused"] = ""
                try:
                    did = self.run_once(cfg)
                except Exception:  # keep the thread alive; the next round retries
                    log.exception("PC content round failed")
                    did = 0
                if not did:
                    self._stop.wait(IDLE_SLEEP_S)
        finally:
            self._state.update(paused="", current="")
            log.info("PC content worker stopped")

    def run_once(self, cfg: dict | None = None) -> int:
        """One batch. Returns how many files it looked at (0 = nothing left to do)."""
        from app.core import indexer

        cfg = cfg or pc_access.config()
        sync_roots()  # takes _ROUND_LOCK itself (via forget) only when a drive was removed
        with _ROUND_LOCK:
            return self._round(cfg)

    def _round(self, cfg: dict) -> int:
        from app.core import indexer

        conn = _conn()
        try:
            self._reset_if_index_lost(conn)
            self._reset_if_ocr_arrived(conn)
            rows = next_batch(conn)
            if not rows:
                sweep_orphans(conn, pc_access.crawler.state()["running"])
                self._state["current"] = ""
                return 0
            visible = _visible_roots()
            run = indexer._Run(settings.db_path, settings.vector_db_dir)
            try:
                for row in rows:
                    if self._stop.is_set() or pause_reason():  # indexer.CANCEL belongs to manual folder scans (Index page STOP); it stays set afterwards
                        break
                    self._one(conn, run, row, cfg, visible)
            finally:
                run.close()
            return len(rows)
        finally:
            conn.close()

    def _reset_if_index_lost(self, conn: sqlite3.Connection) -> None:
        """If the main index was rebuilt (schema upgrade) its rows are gone but content_done still says 'indexed'."""
        from app.db import sqlite_fts

        if not conn.execute("SELECT 1 FROM content_done WHERE state='indexed' LIMIT 1").fetchone():
            return
        c = sqlite_fts.connect(settings.db_path)
        try:
            roots = sqlite_fts.hidden_roots(c)
            have = c.execute(f"SELECT COUNT(*) FROM files WHERE root IN ({_in_list(len(roots))})", roots).fetchone()[0] if roots else 0
        finally:
            c.close()
        if have == 0:
            log.info("index was rebuilt; re-reading PC files")
            conn.execute("DELETE FROM content_done")
            conn.commit()

    def _reset_if_ocr_arrived(self, conn: sqlite3.Connection) -> None:
        """Scanned PDFs and photos read before OCR was installed were recorded as skipped (no text). Once OCR is
        available, queue them again, once."""
        from app.core.parsers import image_parser

        if not image_parser.available() or pc_access._meta(conn, "ocr_seen") == "1":
            return
        cond = " OR ".join(["LOWER(path) LIKE ?"] * 6)
        conn.execute(
            f"DELETE FROM content_done WHERE state='skipped' AND ({cond})",
            ("%.pdf", "%.png", "%.jpg", "%.jpeg", "%.webp", "%.bmp"),
        )
        pc_access._set_meta(conn, "ocr_seen", "1")
        conn.commit()
        log.info("OCR is now available; re-reading scanned files")

    def _one(self, conn, run, row, cfg, visible) -> None:
        from app.core import indexer

        path = Path(row["path"])
        self._state["current"] = path.name
        state, err = "indexed", None
        try:
            ok, why = pc_access.can_read(path)
            drive = _drive_root_for(path, cfg["drives"])
            if not ok or drive is None:
                state, err = "ignored", why or "outside the covered drives"
            elif any(_inside(path, v) for v in visible):
                state, err = "ignored", "already indexed as a folder you added"
            else:
                profile = {"ocr_only": True} if path.suffix.lower() in IMAGE_EXTS else {}
                before = run.keyword_only
                res = indexer._index_one(path, drive, run, profile)
                state = "partial" if run.keyword_only > before else ("indexed" if res == "indexed" else "skipped")
        except Exception as exc:
            indexer._record_error(path, exc)
            state, err = "failed", str(exc)[:200]
        conn.execute(
            "INSERT INTO content_done(path,size,mtime,state,err,at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, state=excluded.state, err=excluded.err, at=excluded.at",
            (str(path), row["size"], row["mtime"], state, err, time.time()),
        )
        conn.commit()
        if state == "failed":
            self._state["failed"] += 1
        elif state in ("indexed", "partial"):
            self._state["indexed"] += 1


def _visible_roots() -> list[str]:
    from app.db import sqlite_fts

    c = sqlite_fts.connect(settings.db_path)
    try:
        return sqlite_fts.root_paths(c)
    finally:
        c.close()


worker = ContentWorker()
