"""Settings > Model (Ollama build): the models Ollama has with their size and expected RAM, the ones the app can
download, removing one, and the optional other OpenAI-compatible server. Downloads reuse setup's job
(POST /setup/model/pull {"repo"}); switching reuses PUT /settings/model. The app never switches models by itself.
No device row: Ollama picks the GPU or CPU on its own."""
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api import routes_setup
from app.config import settings
from app.core import ram
from app.core.llm import external
from app.core.llm.client import current_model

router = APIRouter(prefix="/models", tags=["models"])


def expected_ram_mb(size_mb: int) -> int:
    """ponytail: weights plus ~30% for the context cache and runtime, plus 400 MB; a rough figure for small quantized
    models in Ollama. Replace with a measured figure per model if the guess proves far off."""
    return round(size_mb * 1.3 + 400)


def _tags() -> list[dict]:
    """Ollama's own list ({name, size}); [] when Ollama isn't running."""
    try:
        r = httpx.get(f"{settings.ollama_url}/api/tags", timeout=3)
        r.raise_for_status()
        return r.json().get("models", [])
    except Exception:
        return []


def _entry(name: str, label: str, size_mb: int, installed: bool) -> dict:
    kind = "embed" if routes_setup._tagged(name) == routes_setup._tagged(settings.embedding_model) else "chat"
    return {"name": name, "label": label, "repo": name, "size_mb": size_mb, "ram_mb": expected_ram_mb(size_mb), "kind": kind,
            "installed": installed, "active": installed and routes_setup._tagged(name) == routes_setup._tagged(current_model(local=True)),
            "recommended": kind == "chat" and routes_setup._tagged(name) == routes_setup._tagged(settings.ollama_model)}


@router.get("")
def list_models():
    tags = _tags()
    have = {routes_setup._tagged(m["name"]) for m in tags}
    out = [_entry(m["name"], m["name"], m.get("size", 0) // 1_000_000, True) for m in tags]
    for m in routes_setup.CATALOG:
        if routes_setup._tagged(m["name"]) not in have:
            out.append(_entry(m["name"], m["label"], routes_setup.SIZES_MB.get(m["name"], 0), False))
    return {"models": out, "device": None, "external": external.get(), "memory": ram.status(), "connected": bool(tags)}


class NameRequest(BaseModel):
    name: str


@router.post("/remove")
def remove_model(req: NameRequest):
    """Asks Ollama to delete a model (ollama rm): only one Ollama lists, never the one in use (Ollama's, also while the
    other server answers) or the embedding model."""
    tagged = routes_setup._tagged(req.name)
    if tagged in (routes_setup._tagged(current_model(local=True)), routes_setup._tagged(settings.embedding_model)):
        raise HTTPException(400, "This model is in use. Switch to another model first (Use), then remove it.")
    tags = _tags()
    if not tags:
        raise HTTPException(503, "Ollama isn't running. Start Ollama, then try again.")
    if tagged not in {routes_setup._tagged(m["name"]) for m in tags}:
        raise HTTPException(404, "That model isn't downloaded.")
    try:
        r = httpx.request("DELETE", f"{settings.ollama_url}/api/delete", json={"model": req.name}, timeout=30)
    except httpx.HTTPError:
        raise HTTPException(503, "Ollama isn't running. Start Ollama, then try again.")
    if r.status_code == 404:
        raise HTTPException(404, "Ollama doesn't have that model.")
    if r.status_code >= 400:
        raise HTTPException(502, f"Ollama couldn't remove it: {r.text[:200]}")
    return {"removed": req.name}


class ExternalRequest(BaseModel):
    enabled: bool
    url: str = ""
    model: str = ""
    confirm_remote: bool = False


@router.put("/external")
def set_external(req: ExternalRequest):
    try:
        return external.set(req.enabled, req.url, req.model, req.confirm_remote)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
