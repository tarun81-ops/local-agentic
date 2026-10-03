"""Local speech: faster-whisper turns speech into text, Piper turns text into speech. Both models
load on first use, give their RAM back after an idle spell, and are fetched once by download()
(setup or Settings), never while you are talking."""
import threading
import time

from app.config import settings
from app.core import ram


class VoiceUnavailable(RuntimeError):
    """A voice feature can't run right now. The message says why and what to do."""


class Lazy:
    """A heavy model loaded on first use and dropped after llm_idle_unload_s seconds without use."""

    def __init__(self) -> None:
        self._obj = None
        self._last = 0.0
        self._lock = threading.Lock()
        _REGISTRY.append(self)

    def get(self, load):
        with self._lock:
            if self._obj is None:
                if ram.is_low():
                    raise VoiceUnavailable("Not enough free memory for voice right now. Close some apps and try again.")
                self._obj = load()
                _start_watcher()
            self._last = time.monotonic()
            return self._obj

    def touch(self) -> None:
        self._last = time.monotonic()

    def loaded(self) -> bool:
        return self._obj is not None

    def drop_if_idle(self, now: float) -> bool:
        limit = settings.llm_idle_unload_s
        with self._lock:
            if self._obj is not None and limit > 0 and now - self._last >= limit:
                self._obj = None
                return True
        return False


_REGISTRY: list[Lazy] = []
_watcher: threading.Thread | None = None


def _watch() -> None:
    while True:
        time.sleep(30)
        for lazy in _REGISTRY:
            lazy.drop_if_idle(time.monotonic())


def _start_watcher() -> None:
    global _watcher
    if _watcher is None:
        _watcher = threading.Thread(target=_watch, name="voice-idle", daemon=True)
        _watcher.start()
