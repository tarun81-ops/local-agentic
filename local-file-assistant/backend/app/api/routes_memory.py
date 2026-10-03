from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.assistant import memory_store

router = APIRouter(prefix="/memory", tags=["memory"])


class New(BaseModel):
    text: str
    kind: str = "fact"
    pinned: bool = False


class Patch(BaseModel):
    text: str | None = None
    pinned: bool | None = None
    kind: str | None = None


@router.get("")
def list_memories(q: str = ""):
    return {"memories": memory_store.list_all(q)}


@router.post("")
def add_memory(body: New):
    made = memory_store.add(body.text, body.kind, pinned=body.pinned)
    if made is None:
        raise HTTPException(409, "Already remembered (or empty).")
    return made


@router.patch("/{mem_id}")
def patch_memory(mem_id: int, body: Patch):
    if not memory_store.update(mem_id, body.text, body.pinned, body.kind):
        raise HTTPException(404, "No such memory")
    return {"ok": True}


@router.delete("/{mem_id}")
def delete_memory(mem_id: int):
    memory_store.delete(mem_id)
    return {"ok": True}


@router.post("/wipe")
def wipe_memories():
    return {"deleted": memory_store.wipe()}


@router.get("/export")
def export_memories():
    return {"memories": memory_store.list_all()}
