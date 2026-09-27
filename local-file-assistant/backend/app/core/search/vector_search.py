import httpx

from app.config import settings
from app.db.vector_store import search as _table_search


def embed(texts: list[str]) -> list[list[float]]:
    """Uses Ollama's native /api/embed (not the OpenAI-compat route). Relies on Ollama's default
    idle-timeout eviction rather than forcing keep_alive=0 — that was tried and made indexing much
    slower (every embed call reloaded the 562MB model from disk); at ~600MB this model is cheap
    enough to leave resident for a few minutes between uses."""
    response = httpx.post(
        f"{settings.ollama_url}/api/embed",
        json={"model": settings.embedding_model, "input": texts},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["embeddings"]


def search(table, query: str, limit: int = 5) -> list[dict]:
    return _table_search(table, embed([query])[0], limit)
