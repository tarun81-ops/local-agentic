import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.routes_search import hybrid_search
from app.core.llm.answerer import answer_stream
from app.core.llm.verifier import verify

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    question: str
    model: str | None = None


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _stream(question: str, model: str | None):
    # Search resolves before any LLM call is made, so the UI gets the file/snippet list
    # instantly even while the model is still loading or generating.
    chunks = hybrid_search(question)
    yield _sse("results", {"results": chunks})

    if not chunks:
        yield _sse("done", {"citations": [], "all_verified": False})
        return

    answer_text = ""
    for delta in answer_stream(question, chunks, model=model):
        answer_text += delta
        yield _sse("token", {"delta": delta})

    yield _sse("done", verify(answer_text, chunks))


@router.post("")
async def chat(req: ChatRequest):
    return StreamingResponse(_stream(req.question, req.model), media_type="text/event-stream")
