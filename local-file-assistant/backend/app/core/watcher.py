"""Watchdog-based folder watcher: reindexes a file when it's created or changed."""
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from app.config import settings
from app.core.indexer import PARSERS, index_file


class _Handler(FileSystemEventHandler):
    # ponytail: reindexes on every raw fs event (no debounce) — a file saved by an editor
    # that writes+renames can trigger two passes. index_file() is hash-checked and idempotent,
    # so the extra pass just costs a skip. Add debouncing if watched folders get large/busy.
    def _maybe_index(self, src_path: str) -> None:
        path = Path(src_path)
        if path.suffix.lower() in PARSERS and path.is_file():
            index_file(path, settings.db_path, settings.vector_db_dir)

    def on_created(self, event):
        if not event.is_directory:
            self._maybe_index(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._maybe_index(event.src_path)


def start_watcher() -> Observer | None:
    folders = [f.strip() for f in settings.watch_folders.split(",") if f.strip()]
    folders = [f for f in folders if Path(f).is_dir()]
    if not folders:
        return None
    observer = Observer()
    handler = _Handler()
    for folder in folders:
        observer.schedule(handler, folder, recursive=True)
    observer.start()
    return observer
