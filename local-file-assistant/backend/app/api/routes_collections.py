from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api import routes_search
from app.core.assistant import saved_searches

router = APIRouter(prefix="/collections", tags=["collections"])


class New(BaseModel):
    name: str
    query: str
    root: str | None = None
    pinned: bool = True


class Patch(BaseModel):
    name: str | None = None
    query: str | None = None
    root: str | None = None
    pinned: bool | None = None


@router.get("")
def list_collections():
    return {"collections": saved_searches.list_all()}


@router.post("")
def create_collection(body: New):
    try:
        return saved_searches.create(body.name, body.query, body.root, body.pinned)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.patch("/{coll_id}")
def update_collection(coll_id: int, body: Patch):
    try:
        found = saved_searches.update(coll_id, body.name, body.query, body.root, body.pinned)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if not found:
        raise HTTPException(404, "No such collection")
    return saved_searches.get(coll_id)


@router.delete("/{coll_id}")
def delete_collection(coll_id: int):
    saved_searches.delete(coll_id)
    return {"ok": True}


@router.get("/{coll_id}/run")
def run_collection(coll_id: int):
    """Runs the saved search now: the same one-result-per-file shape as GET /search."""
    coll = saved_searches.get(coll_id)
    if coll is None:
        raise HTTPException(404, "No such collection")
    return {"collection": coll, **routes_search.search(coll["query"], root=coll["root"])}
