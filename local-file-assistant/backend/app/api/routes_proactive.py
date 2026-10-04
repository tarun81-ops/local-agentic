from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.core.assistant import briefing

router = APIRouter(prefix="/proactive", tags=["proactive"])


@router.get("/settings")
def get_settings():
    return briefing.settings()


@router.put("/settings")
def put_settings(body: dict):
    try:
        return briefing.save(body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/preview")
def preview():
    """What the briefing would say right now. Sends nothing and records nothing."""
    data = briefing.build(datetime.now())
    return {"empty": briefing.is_empty(data), "text": briefing.phrase(data)}
