from typing import Literal

from pydantic import BaseModel


class OrganizeAction(BaseModel):
    op: Literal["move", "delete"]
    path: str
    to: str | None = None  # required for "move"
    reason: str | None = None
    group: str | None = None  # destination folder, or "Duplicates" / "Backup & temp files"


class OrganizePlan(BaseModel):
    summary: str
    root: str | None = None
    actions: list[OrganizeAction]
    dropped_invalid: int = 0  # model suggestions that were unusable (unknown file, bad folder)


class PlanRequest(BaseModel):
    root: str | None = None


class ApplyRequest(BaseModel):
    actions: list[OrganizeAction]


class UndoRequest(BaseModel):
    batch: str | None = None  # None = the most recent batch with moves
