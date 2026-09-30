import json
import threading

import httpx
from openai import OpenAI

from app.config import settings

_SETTINGS_FILE = "settings.json"
_lock = threading.Lock()


def get_client() -> OpenAI:
    """Ollama's OpenAI-compatible endpoint. Swap base_url to move to OpenVINO Model Server."""
    return OpenAI(base_url=settings.ollama_base_url, api_key="ollama")


def current_model() -> str:
    """The model picked in Settings (saved in the data dir), else OLLAMA_MODEL."""
    path = settings.data_dir / _SETTINGS_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("model") or settings.ollama_model
    except (OSError, ValueError):
        return settings.ollama_model


def set_model(name: str) -> None:
    with _lock:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        path = settings.data_dir / _SETTINGS_FILE
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data["model"] = name
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def ollama_status() -> dict:
    """Whether the model server answers, and which models it has. (Named for Ollama, but
    works for any OpenAI-compatible server when llm_provider is "openai".)"""
    try:
        if settings.llm_provider != "ollama":
            r = httpx.get(f"{settings.ollama_base_url.rstrip('/')}/models", timeout=3)
            r.raise_for_status()
            return {"connected": True, "models": sorted(m["id"] for m in r.json().get("data", []))}
        r = httpx.get(f"{settings.ollama_url}/api/tags", timeout=3)
        r.raise_for_status()
        return {"connected": True, "models": sorted(m["name"] for m in r.json().get("models", []))}
    except Exception as exc:
        return {"connected": False, "models": [], "error": str(exc)}
