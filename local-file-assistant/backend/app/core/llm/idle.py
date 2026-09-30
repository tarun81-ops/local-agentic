"""Unloads the chat model from Ollama after a quiet spell, giving its RAM back to the rest
of the laptop. It is loaded again automatically (a few seconds) on the next question."""
import logging
import threading
import time

import httpx

from app.config import settings
from app.core.llm.client import current_model

log = logging.getLogger(__name__)

_last_used: float | None = None
_lock = threading.Lock()
_stop = threading.Event()


def touch() -> None:
    """Call whenever the chat model is used."""
    global _last_used
    with _lock:
        _last_used = time.monotonic()


def _due(now: float) -> bool:
    with _lock:
        return _last_used is not None and settings.llm_idle_unload_s > 0 and now - _last_used >= settings.llm_idle_unload_s


def unload() -> bool:
    """Asks Ollama to drop the model now (keep_alive 0). Other backends: nothing to do."""
    global _last_used
    with _lock:
        _last_used = None
    if settings.llm_provider != "ollama":
        return False
    try:
        httpx.post(f"{settings.ollama_url}/api/generate", json={"model": current_model(), "keep_alive": 0}, timeout=10)
        log.info("unloaded %s after %ss idle", current_model(), settings.llm_idle_unload_s)
        return True
    except httpx.HTTPError as exc:
        log.warning("could not unload the model: %s", exc)
        return False


def _loop() -> None:
    while not _stop.wait(30):
        if _due(time.monotonic()):
            unload()


def start() -> None:
    _stop.clear()
    threading.Thread(target=_loop, name="llm-idle", daemon=True).start()


def stop() -> None:
    _stop.set()
