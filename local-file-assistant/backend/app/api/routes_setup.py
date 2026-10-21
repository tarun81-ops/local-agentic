"""First-run setup (ui/src/pages/setup.js): what is still missing before the app can answer, and the model download.

Ollama build: the engine is Ollama. If it isn't there, setup offers the official download page (opened in the browser;
the app never runs an installer itself). Models are pulled through Ollama's own POST /api/pull, whose streamed
progress this route passes on. The download only starts when the user clicks it."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import settings
from app.core import pc_access, prefs, ram
from app.core.llm.client import current_model, ollama_status

log = logging.getLogger(__name__)
router = APIRouter(prefix="/setup", tags=["setup"])

OLLAMA_DOWNLOAD = "https://ollama.com/download"
# Approximate download sizes (Ollama reports the exact total once the pull starts), for the Download button.
SIZES_MB = {"qwen3-vl:2b-instruct": 1890, "granite-embedding:278m": 562, "qwen3-vl:4b-instruct": 3300}
# What Settings > Model offers to download. The 4B is bigger than the default: offered, never recommended.
CATALOG = [{"name": "qwen3-vl:2b-instruct", "label": "Qwen3-VL 2B"}, {"name": "qwen3-vl:4b-instruct", "label": "Qwen3-VL 4B"}]


def recommended_chat(total_mb: int) -> dict:
    """The current default on every PC: qwen3-vl:2b is the smallest model that reads images, and setup never
    recommends anything bigger. Under 8 GB it still works, but slowly, and the wizard says so."""
    name = settings.ollama_model
    why = f"for a PC with {round(total_mb / 1024)} GB of RAM" + (" (it will be slow with this little RAM)" if total_mb < 7500 else "")
    return {"name": name, "label": name, "size_mb": SIZES_MB.get(name, 0), "why": why}


def _tagged(name: str) -> str:
    """Ollama lists "llama3" as "llama3:latest"."""
    return name if ":" in name else f"{name}:latest"


def plan(served: list[str]) -> list[dict]:
    """The pulls still needed: the chat model and the embedding model, unless Ollama already has them."""
    have = {_tagged(m) for m in served}
    out = []
    for name in dict.fromkeys([current_model(local=True), settings.embedding_model]):  # Ollama's own, not the other server's
        if _tagged(name) not in have:
            out.append({"name": name, "label": name, "repo": name, "size_mb": SIZES_MB.get(name, 0), "command": f"ollama pull {name}"})
    return out


def _installed_exe() -> bool:
    """Ollama's Windows installer puts it in %LOCALAPPDATA%\\Programs\\Ollama."""
    base = os.environ.get("LOCALAPPDATA")
    return bool(base) and (Path(base) / "Programs" / "Ollama" / "ollama.exe").is_file()


def engine() -> dict:
    if settings.llm_provider != "ollama":  # another OpenAI-compatible server (LLM_PROVIDER=openai): nothing to set up here
        ok = ollama_status()["connected"]
        return {"name": "Model server", "state": "ready" if ok else "failed", "ok": ok,
                "detail": "" if ok else f"The model server at {settings.ollama_base_url} doesn't answer. Start it, then click Check again."}
    try:
        r = httpx.get(f"{settings.ollama_url}/api/version", timeout=3)
        r.raise_for_status()
        return {"name": "Ollama", "state": "ready", "ok": True, "detail": f"Version {r.json().get('version', '?')}"}
    except Exception:
        pass
    if _installed_exe():
        return {"name": "Ollama", "state": "not running", "ok": False,
                "detail": "Ollama is installed but not running. Open Ollama from the Start menu, then click Check again."}
    return {"name": "Ollama", "state": "missing", "ok": False,
            "detail": "Ollama runs the model on this PC. Download and install it, then click Check again.",
            "help": {"label": "Download Ollama", "url": OLLAMA_DOWNLOAD}}


# ---------- the download job: one at a time, keeps running if the wizard is closed ----------

_lock = threading.Lock()
_job: dict = {"status": "idle"}  # idle | running | done | failed | cancelled
_cancel = threading.Event()


def pull_lines(name: str):
    """Ollama's streamed pull: one JSON object per line ({"status", "digest", "total", "completed"} or {"error"}).
    Closing the stream (on cancel) makes Ollama stop the download."""
    with httpx.stream("POST", f"{settings.ollama_url}/api/pull", json={"model": name, "stream": True},
                      timeout=httpx.Timeout(30, read=None)) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line.strip():
                yield json.loads(line)


def apply_progress(layers: dict, msg: dict) -> tuple[int, int]:
    """Adds one /api/pull message to {digest: (completed, total)} and returns (completed, total) bytes over all layers."""
    if msg.get("digest") and msg.get("total"):
        layers[msg["digest"]] = (msg.get("completed", 0), msg["total"])
    return sum(c for c, _ in layers.values()), sum(t for _, t in layers.values())


def _run(items: list[dict]) -> None:
    try:
        for n, item in enumerate(items, 1):
            _job.update(step=n, steps=len(items), model=item["label"], percent=0, mb=0, total_mb=item["size_mb"], detail="connecting to ollama.com")
            layers: dict = {}
            for msg in pull_lines(item["name"]):
                if _cancel.is_set():
                    _job.update(status="cancelled", detail="Download cancelled. Click Download to start again.")
                    return  # leaving the loop closes the stream, which stops Ollama's download
                if msg.get("error"):
                    _job.update(status="failed", detail=f"Ollama couldn't download {item['label']}: {msg['error']}. Check the internet connection and click Download to try again.")
                    return
                done, total = apply_progress(layers, msg)
                if total:
                    _job.update(mb=done // 1_000_000, total_mb=total // 1_000_000, percent=min(99, done * 100 // total))
                _job["detail"] = msg.get("status", "")
        _job.update(status="done", percent=100, detail="Downloaded. You can ask questions now.")
    except Exception as exc:
        log.exception("model download failed")
        _job.update(status="failed", detail="The download stopped: Ollama didn't answer. Check that Ollama is running and the internet connection works, then click Download again." if isinstance(exc, httpx.HTTPError) else f"The download stopped: {exc}. Click Download to try again.")


def start_pull(items: list[dict] | None = None) -> dict:
    """items: what to pull; None = whatever setup still needs (plan())."""
    with _lock:
        if _job["status"] == "running":
            return dict(_job)
        status = ollama_status()
        if not status["connected"]:
            _job.clear()
            _job.update(status="failed", detail="Ollama isn't running. Finish the Model engine step first.")
            return dict(_job)
        items = items if items is not None else plan(status["models"])
        if not items:
            _job.clear()
            _job.update(status="done", percent=100, detail="The models are already downloaded.")
            return dict(_job)
        _cancel.clear()
        _job.clear()
        _job.update(status="running", percent=0, mb=0, total_mb=sum(i["size_mb"] for i in items), detail="starting")
        threading.Thread(target=_run, args=(items,), name="model-pull", daemon=True).start()
        return dict(_job)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# ---------- routes ----------

@router.get("/status")
def get_status():
    """Engine, models, PC access, and what is still missing (an empty `missing` means the app can answer)."""
    mem = ram.status()
    eng = engine()
    served = ollama_status() if eng["ok"] else {"connected": False, "models": []}
    todo = plan(served["models"]) if served["connected"] and settings.llm_provider == "ollama" else []
    missing = ([] if eng["ok"] else ["engine", "model"]) + (["model"] if todo else [])
    pc = pc_access.config()
    # An install that already works and has PC access on was set up before this wizard existed: don't show it again.
    done = prefs.get("setup_done")
    if done is None:
        done = not missing and pc["enabled"]
    return {
        "done": bool(done),
        "engine": eng,
        "model": {
            "recommended": recommended_chat(mem["total_mb"]),
            "installed": served["models"],
            "needed": todo,
            "source": "ollama.com",
            "pull": dict(_job),
        },
        "folders": {"pc_access": pc["enabled"], "drives": pc["drives"]},
        "memory": mem,
        "missing": missing,
    }


class PullRequest(BaseModel):
    repo: str | None = None  # a CATALOG model (Settings > Model); none = what setup still needs


@router.post("/model/pull")
def pull(req: PullRequest | None = None):
    """Starts the download (or joins the running one) and streams `progress` events until it ends with
    `done`, `failed` or `cancelled`. Leaving the page doesn't stop it; POST /setup/model/pull/cancel does."""
    items = None
    if req and req.repo:
        if req.repo not in {m["name"] for m in CATALOG}:
            raise HTTPException(400, "That model isn't in the list this app can download.")
        items = [{"name": req.repo, "label": req.repo, "repo": req.repo, "size_mb": SIZES_MB.get(req.repo, 0), "command": f"ollama pull {req.repo}"}]
    first = start_pull(items)

    def stream():
        state = first
        while state["status"] == "running":
            yield _sse("progress", state)
            time.sleep(0.5)
            state = dict(_job)
        yield _sse(state["status"], state)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/model/pull/cancel")
def cancel():
    _cancel.set()
    return {"ok": True}


@router.post("/done")
def finish():
    prefs.set("setup_done", True)
    return {"done": True}
