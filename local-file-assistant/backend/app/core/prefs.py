"""User preferences in settings.json (data dir). Tiny JSON key/value store."""
import json
import threading

from app.config import settings

_FILE = "settings.json"
_lock = threading.Lock()


def _read() -> dict:
    try:
        return json.loads((settings.data_dir / _FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def get(key: str, default=None):
    return _read().get(key, default)


def set(key: str, value) -> None:
    with _lock:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        data = _read()
        data[key] = value
        (settings.data_dir / _FILE).write_text(json.dumps(data, indent=2), encoding="utf-8")
