import json

import httpx
from openai import OpenAI

from app.config import settings
from app.core import prefs


def get_client() -> OpenAI:
    """Ollama's OpenAI-compatible endpoint. Swap base_url to move to OpenVINO Model Server."""
    # No retries: a second attempt doubles the wait, and the caller reports the failure anyway.
    return OpenAI(
        base_url=settings.ollama_base_url,
        api_key="ollama",
        timeout=httpx.Timeout(settings.llm_timeout_s, connect=5),
        max_retries=0,
    )


def current_model() -> str:
    """The model picked in Settings (saved in the data dir), else OLLAMA_MODEL."""
    return prefs.get("model") or settings.ollama_model


def set_model(name: str) -> None:
    prefs.set("model", name)


def llm_json(prompt: str, max_tokens: int = 300) -> dict:
    """One non-streamed call that must answer with a JSON object; {} if it doesn't."""
    from app.core.llm import idle

    idle.touch()
    r = get_client().chat.completions.create(
        model=current_model(),
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    try:
        data = json.loads(r.choices[0].message.content or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


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
