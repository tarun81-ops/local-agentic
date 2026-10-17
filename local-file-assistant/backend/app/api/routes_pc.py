"""Whole-PC access: switch it on, choose the drives, run or stop the catalog scan, and check what it knows."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core import pc_access, pc_content

router = APIRouter(prefix="/pc", tags=["pc access"])


class PcSettings(BaseModel):
    enabled: bool | None = None
    drives: list[str] | None = None
    extra_excludes: list[str] | None = None
    read_content: bool | None = None
    read_images: bool | None = None


@router.get("")
def get_status():
    """Switch state, covered drives, the drives that could be added, catalog size and scan progress."""
    return pc_access.status()


@router.put("/settings")
def put_settings(req: PcSettings):
    """Saves the switch / drives / extra excluded folder names. Turning it on starts the first scan."""
    before = pc_access.enabled()
    cfg = pc_access.set_config(req.enabled, req.drives, req.extra_excludes, req.read_content, req.read_images)
    if cfg["enabled"] and not cfg["drives"]:
        raise HTTPException(400, "No drives found to cover. Pick a drive or folder.")
    if cfg["enabled"] and (not before or req.drives is not None):
        pc_access.crawler.start()
    if not cfg["enabled"]:
        pc_access.crawler.stop()
    elif req.drives is not None or not before:
        pc_content.sync_roots()  # new drives become hidden roots, dropped drives are forgotten
    pc_content.worker.start()  # no-op if running; it idles by itself while PC access or reading is off
    return pc_access.status()


@router.post("/scan")
def scan_now():
    if not pc_access.enabled():
        raise HTTPException(400, "PC access is off.")
    return {"started": pc_access.crawler.start(), **pc_access.status()}


@router.post("/stop")
def stop_scan():
    pc_access.crawler.stop()
    return pc_access.status()


@router.get("/find")
def find(q: str, limit: int = 15):
    """Filename search over the catalog (what the assistant uses first)."""
    if not pc_access.enabled():
        raise HTTPException(400, "PC access is off.")
    return {"results": pc_access.find(pc_access.keywords(q), limit=max(1, min(limit, 50)))}


@router.post("/forget")
def forget_contents():
    """Removes everything read from the PC drives from the index (names catalog and files on disk stay)."""
    removed = pc_content.forget()
    return {"removed": removed, **pc_access.status()}
