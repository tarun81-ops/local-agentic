from typing import Literal

from pydantic import BaseModel


class OrganizeAction(BaseModel):
    op: Literal["move", "copy", "rename", "mkdir", "delete"]
    path: str  # the file or folder; for "mkdir" the folder to create
    to: str | None = None  # move/copy: the full destination path (older callers also send a rename's new name here)
    to_folder: str | None = None  # move/copy into this folder, keeping the name ("name (2)" if it's taken)
    new_name: str | None = None  # rename: the new name, in the same folder
    reason: str | None = None
    group: str | None = None  # destination folder, or "Duplicates" / "Backup & temp files"
    selected: bool = True  # ticked when the plan is shown; risky suggestions (old large files) start unticked
    size: int | None = None  # bytes, so the UI can show what a delete frees


class OrganizePlan(BaseModel):
    summary: str
    root: str | None = None
    actions: list[OrganizeAction]
    dropped_invalid: int = 0  # model suggestions that were unusable (unknown file, bad folder)
    kind: str = "tidy"  # rule-based cleanup (Tidy-up); the model-proposed "ai" plan was removed
    freed_bytes: int = 0  # what the ticked Recycle Bin actions free
    scanned: int = 0  # files looked at
    by_type: list[dict] = []  # [{type, bytes, count}], biggest first: what takes the space in this folder


TIDY_CHECKS = ("duplicates", "partial", "old_large", "empty", "sort")


class TidyRequest(BaseModel):
    folder: str | None = None  # None = the Downloads folder
    large_mb: int = 100
    old_days: int = 90
    checks: list[str] = list(TIDY_CHECKS)  # which kinds of suggestions to make


class ApplyRequest(BaseModel):
    actions: list[OrganizeAction]


class UndoRequest(BaseModel):
    batch: str | None = None  # None = the most recent batch with moves
