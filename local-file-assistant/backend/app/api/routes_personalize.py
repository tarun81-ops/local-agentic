from fastapi import APIRouter, HTTPException

from app.core import personalize

router = APIRouter(prefix="/personalize", tags=["personalize"])


@router.get("")
def get_personalize():
    return personalize.get_all()


@router.put("")
def put_personalize(body: dict):
    try:
        return personalize.update(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
