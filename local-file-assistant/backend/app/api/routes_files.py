from fastapi import APIRouter

router = APIRouter(prefix="/files", tags=["files"])


@router.get("/status")
async def status():
    raise NotImplementedError
