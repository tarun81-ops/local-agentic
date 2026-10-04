import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.core.assistant import learning

router = APIRouter(prefix="/learning", tags=["learning"])


class Feedback(BaseModel):
    msg_id: int
    rating: int  # 1 helpful, -1 not helpful, 0 take the mark back


@router.post("/feedback")
def feedback(body: Feedback):
    try:
        learning.set_feedback(body.msg_id, body.rating)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except LookupError:
        raise HTTPException(404, "No such answer") from None
    return {"ok": True}


@router.get("/stats")
def stats():
    return learning.stats()


@router.get("/export", response_class=PlainTextResponse)
def export():
    """Not-helpful questions as JSON lines, ready to extend eval/questions.jsonl."""
    return PlainTextResponse("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in learning.export_questions()), media_type="application/x-ndjson")


@router.post("/wipe")
def wipe():
    """Forgets every recorded open and rating."""
    return learning.wipe()
