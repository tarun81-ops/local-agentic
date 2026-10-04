from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.assistant import actions
from app.core.assistant.tools.registry import ToolError

router = APIRouter(prefix="/actions", tags=["actions"])


class Propose(BaseModel):
    tool: str
    args: dict
    conv_id: int | None = None


def _call(fn, *args):
    try:
        return fn(*args)
    except actions.NotFound:
        raise HTTPException(404, "No such action") from None
    except actions.Conflict as exc:
        raise HTTPException(409, str(exc)) from None
    except ToolError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("")
def history(conv_id: int | None = None):
    return {"actions": actions.history(conv_id)}


@router.post("/propose")
def propose(body: Propose):
    """Validates and stores a proposal. Nothing runs until /approve."""
    return _call(actions.propose, body.tool, body.args, body.conv_id)


@router.post("/{rid}/approve")
def approve(rid: int):
    return _call(actions.approve, rid)


@router.post("/{rid}/reject")
def reject(rid: int):
    return _call(actions.reject, rid)


@router.post("/{rid}/undo")
def undo(rid: int):
    return _call(actions.undo, rid)
