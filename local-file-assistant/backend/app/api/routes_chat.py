import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core import memory
from app.core.llm.answerer import answer_stream
from app.core.llm.verifier import verify
from app.core.search.hybrid import hybrid_search

log = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    question: str
    root: str | None = None
    model: str | None = None


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _public(chunk: dict) -> dict:
    return {k: chunk[k] for k in ("path", "name", "loc_kind", "loc_no", "snippet", "match")}


def _stream(req: ChatRequest):
    # Search resolves before any LLM call, so the UI shows matching files straight away
    # even while the model is still loading.
    try:
        found = hybrid_search(req.question, root=req.root, limit=6)
    except Exception as exc:  # e.g. a locked or damaged index: say so, don't leave "Searching…" up
        log.exception("search failed")
        yield _sse("error", {"detail": f"Couldn't search your files: {exc}"})
        return
    chunks = found["results"]
    yield _sse("results", {"results": [_public(c) for c in chunks], "semantic": found["semantic"]})

    if not chunks:
        yield _sse("done", {"citations": [], "uncited": [], "all_verified": False, "answer": "", "no_results": True, "truncated": False})
        return

    mem = memory.status()
    if mem["low"]:
        yield _sse("warning", {"detail": f"Only {mem['free_mb']} MB of RAM free; the answer may be slow. Closing other apps helps."})

    answer = ""
    deltas = answer_stream(req.question, chunks, model=req.model)
    try:
        while True:
            try:
                delta = next(deltas)
            except StopIteration as stop:  # the stream's return value: cut off at the token cap?
                truncated = bool(stop.value)
                break
            answer += delta
            yield _sse("token", {"delta": delta})
    except Exception as exc:
        log.exception("answer generation failed")
        yield _sse("error", {"detail": f"The local model didn't answer: {exc}"})
        return

    result = verify(answer, chunks)
    # Attach a snippet to each cited source for the source cards.
    snippets = {(c["path"], c["loc_kind"], int(c["loc_no"])): c["snippet"] for c in chunks}
    for cit in result["citations"]:
        if cit.get("path"):
            cit["snippet"] = snippets.get((cit["path"], cit["loc_kind"], cit["loc_no"]), "")
    yield _sse("done", {**result, "answer": answer, "truncated": truncated})


@router.post("")
def chat(req: ChatRequest):
    return StreamingResponse(_stream(req), media_type="text/event-stream")
