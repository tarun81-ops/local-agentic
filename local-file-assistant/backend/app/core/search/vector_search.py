import httpx

from app.config import settings
from app.core import personalize
from app.db.vector_store import search as _table_search


def embed(texts: list[str], timeout: float = 120) -> list[list[float]]:
    """Embeddings in batches, so one long document can't blow the timeout.

    Ollama: native /api/embed. The model stays resident for embed_keep_alive between batches
    (keep_alive=0 reloaded the ~600MB model on every call and made indexing much slower), then
    frees its RAM a couple of minutes after indexing stops.
    Other providers: the OpenAI-compatible /v1/embeddings (e.g. OpenVINO Model Server)."""
    vectors: list[list[float]] = []
    size = max(1, settings.embed_batch_size)
    with httpx.Client(timeout=timeout) as client:
        for start in range(0, len(texts), size):
            batch = texts[start : start + size]
            if settings.llm_provider == "ollama":
                response = client.post(
                    f"{settings.ollama_url}/api/embed",
                    json={"model": settings.embedding_model, "input": batch, "keep_alive": personalize.effective("embed_keep_alive")},
                )
                response.raise_for_status()
                vectors.extend(response.json()["embeddings"])
            else:
                response = client.post(
                    f"{settings.ollama_base_url.rstrip('/')}/embeddings",
                    json={"model": settings.embedding_model, "input": batch},
                )
                response.raise_for_status()
                data = sorted(response.json()["data"], key=lambda d: d["index"])
                vectors.extend(d["embedding"] for d in data)
    return vectors


def search(table, query: str, limit: int = 8, root: str | None = None) -> list[dict]:
    # A short timeout: search must stay usable when the embedder is slow; keyword results still come back.
    return _table_search(table, embed([query], timeout=15)[0], limit, root)
