"""What the user is looking at when they summon the assistant, and pasting an answer back.
Electron's main process calls these (it owns the clipboard): capture, read the clipboard, insert."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core import prefs
from app.core.assistant import context_win

router = APIRouter(prefix="/context", tags=["context"])

# Apps we never copy from: password managers, and terminals where Ctrl+C means "interrupt".
DEFAULT_IGNORED = [
    "keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe", "lastpass.exe", "dashlane.exe",
    "windowsterminal.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe", "putty.exe",
]

_last_hwnd: int | None = None  # the only window /insert may paste into


class Insert(BaseModel):
    hwnd: int


@router.post("/capture")
def capture():
    """Notes the foreground window and sends it Ctrl+C. The caller reads the clipboard afterwards."""
    global _last_hwnd
    _last_hwnd = None
    if not prefs.get("capture_enabled", True):
        return {"ignored": True}
    fg = context_win.foreground()
    if fg is None:
        return {}
    if fg["app"] in {a.lower() for a in prefs.get("ignored_apps", DEFAULT_IGNORED)}:
        return {"ignored": True}
    context_win.wait_modifiers_released()
    context_win.send_ctrl_c()
    _last_hwnd = fg["hwnd"]
    return fg


@router.post("/insert")
def insert(body: Insert):
    """Pastes (Ctrl+V) into the window captured last, never any other. The caller has put the text on the clipboard."""
    if body.hwnd != _last_hwnd:
        raise HTTPException(409, "That window is not the one the assistant was opened over.")
    if not context_win.focus(body.hwnd):
        raise HTTPException(409, "Couldn't bring that window back to the front.")
    context_win.wait_modifiers_released()
    context_win.send_ctrl_v()
    return {"ok": True}
