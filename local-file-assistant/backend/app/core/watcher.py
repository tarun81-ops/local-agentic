"""Keeps the index in step with the disk: re-indexes created/changed files and forgets deleted
or moved-away ones, for every indexed folder. Events are debounced, and the work runs on one
worker thread, not inside watchdog's event thread."""
import logging
import os
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from app.config import settings
from app.core import indexer
from app.db import sqlite_fts

log = logging.getLogger(__name__)

DEBOUNCE_S = 1.5  # editors often write, rename and touch a file in quick succession


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: "FolderWatcher"):
        self.w = watcher

    def on_created(self, event):
        if not event.is_directory:
            self.w.queue(event.src_path, "index")

    def on_modified(self, event):
        if not event.is_directory:
            self.w.queue(event.src_path, "index")

    def on_deleted(self, event):
        self.w.queue(event.src_path, "remove_tree" if event.is_directory else "remove")

    def on_moved(self, event):
        if event.is_directory:
            self.w.queue(event.src_path, "remove_tree")
            self.w.queue(event.dest_path, "index_tree")
        else:
            self.w.queue(event.src_path, "remove")
            self.w.queue(event.dest_path, "index")


class FolderWatcher:
    def __init__(self):
        self._observer: Observer | None = None
        self._pending: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None

    # -- lifecycle ----------------------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        self._worker = threading.Thread(target=self._drain, name="index-watcher", daemon=True)
        self._worker.start()
        self.refresh()

    def stop(self) -> None:
        self._stop.set()
        self._stop_observer()

    def refresh(self) -> None:
        """(Re)watches the current set of indexed folders; call after one is added or removed."""
        self._stop_observer()
        conn = sqlite_fts.connect(settings.db_path)
        try:
            for extra in (f.strip() for f in settings.watch_folders.split(",")):
                if extra and Path(extra).is_dir():
                    sqlite_fts.add_root(conn, Path(extra).resolve())
            folders = [r for r in sqlite_fts.root_paths(conn) if Path(r).is_dir()]
        finally:
            conn.close()
        if not folders:
            return
        observer = Observer()
        handler = _Handler(self)
        for folder in folders:
            observer.schedule(handler, folder, recursive=True)
        observer.start()
        self._observer = observer

    def _stop_observer(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

    # -- event queue --------------------------------------------------------------
    def queue(self, path: str, action: str) -> None:
        with self._lock:
            self._pending[path] = (action, time.monotonic())

    def _due(self) -> list[tuple[str, str]]:
        now = time.monotonic()
        with self._lock:
            ready = [(p, a) for p, (a, t) in self._pending.items() if now - t >= DEBOUNCE_S]
            for p, _ in ready:
                del self._pending[p]
        return ready

    def _drain(self) -> None:
        while not self._stop.wait(0.5):
            for path, action in self._due():
                try:
                    self._apply(Path(path), action)
                except Exception:
                    log.exception("watcher failed on %s (%s)", path, action)

    def _apply(self, path: Path, action: str) -> None:
        db, vdb = settings.db_path, settings.vector_db_dir
        if action == "index":
            if path.is_file() and indexer.is_indexable(path):
                indexer.index_file(path, db, vdb)
        elif action == "remove":
            indexer.remove_file(path, db, vdb)
        elif action == "remove_tree":
            conn = sqlite_fts.connect(db)
            try:
                prefix = str(path) + os.sep
                gone = [p for p in sqlite_fts.indexed_paths(conn) if p.startswith(prefix)]
            finally:
                conn.close()
            for p in gone:
                indexer.remove_file(p, db, vdb)
        elif action == "index_tree" and path.is_dir():
            for child in path.rglob("*"):
                if child.is_file() and indexer.is_indexable(child):
                    indexer.index_file(child, db, vdb)


watcher = FolderWatcher()
