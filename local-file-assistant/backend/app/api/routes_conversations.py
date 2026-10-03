from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.assistant import conversation

router = APIRouter(prefix="/conversations", tags=["conversations"])


class Patch(BaseModel):
    title: str | None = None
    pinned: bool | None = None


class Import(BaseModel):
    chats: list[dict]


@router.get("")
def list_conversations():
    return {"conversations": conversation.list_all()}


@router.post("")
def create_conversation():
    return conversation.create()


@router.post("/import")
def import_conversations(body: Import):
    return {"imported": conversation.import_chats(body.chats)}


@router.get("/{conv_id}")
def get_conversation(conv_id: int):
    conv = conversation.get(conv_id)
    if conv is None:
        raise HTTPException(404, "No such conversation")
    return conv


@router.patch("/{conv_id}")
def patch_conversation(conv_id: int, body: Patch):
    if not conversation.update(conv_id, body.title, body.pinned):
        raise HTTPException(404, "No such conversation")
    return {"ok": True}


@router.delete("/{conv_id}")
def delete_conversation(conv_id: int):
    conversation.delete(conv_id)
    return {"ok": True}
