from pathlib import Path

from fastapi import APIRouter

from app.config import settings
from app.core.file_ops.file_mover import move
from app.core.file_ops.safe_delete import trash
from app.core.llm.organizer import propose_plan
from app.models.organize import OrganizePlan

router = APIRouter(prefix="/organize", tags=["organize"])


@router.post("/plan")
async def propose(files: list[dict]) -> OrganizePlan:
    return propose_plan(files)


@router.post("/apply")
async def apply(plan: OrganizePlan) -> list[dict]:
    """Only called after the user approves the plan shown by /plan — never runs on its own."""
    results = []
    for action in plan.actions:
        if action.op == "move":
            move(Path(action.path), Path(action.to), settings.data_dir)
        elif action.op == "delete":
            trash(Path(action.path))
        results.append({"op": action.op, "path": action.path, "to": action.to})
    return results
