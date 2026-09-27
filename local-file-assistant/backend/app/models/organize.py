from typing import Literal

from pydantic import BaseModel


class OrganizeAction(BaseModel):
    op: Literal["move", "delete"]
    path: str
    to: str | None = None  # required for "move", unused for "delete"


class OrganizePlan(BaseModel):
    summary: str
    actions: list[OrganizeAction]
    dropped_invalid: int = 0  # actions the organizer proposed for files it wasn't shown
