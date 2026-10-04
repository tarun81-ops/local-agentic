"""Windows glue for the floating assistant: which window is in front, copy its selection, paste
back. ctypes only (no extra packages). On other platforms every call reports "nothing", so the
API and tests run anywhere."""
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

import psutil

IS_WIN = sys.platform == "win32"
VK_CONTROL, VK_C, VK_V = 0x11, 0x43, 0x56
_MODIFIERS = (0x10, 0x11, 0x12, 0x5B, 0x5C)  # shift, ctrl, alt, left/right win
_KEYUP, _KEYBOARD = 0x0002, 1

if IS_WIN:
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _MOUSEINPUT(ctypes.Structure):  # only here so the union has its real size
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]

        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def foreground() -> dict | None:
    """{hwnd, title, app} of the window in front (app = lower-case exe name), or None."""
    if not IS_WIN:
        return None
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        app = Path(psutil.Process(pid.value).exe()).name.lower()
    except (psutil.Error, OSError):
        app = ""
    return {"hwnd": int(hwnd), "title": buf.value, "app": app}


def wait_modifiers_released(timeout: float = 1.5) -> bool:
    """The shortcut that summoned us (Ctrl+Shift+Space) is still held when we run; Ctrl+C sent now
    would arrive as Ctrl+Shift+C. Polls until no modifier is down."""
    if not IS_WIN:
        return True
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in _MODIFIERS):
            return True
        time.sleep(0.02)
    return False


def _chord(vk: int) -> None:
    if not IS_WIN:
        return
    keys = [(VK_CONTROL, 0), (vk, 0), (vk, _KEYUP), (VK_CONTROL, _KEYUP)]
    inputs = (_INPUT * len(keys))(*[_INPUT(type=_KEYBOARD, ki=_KEYBDINPUT(wVk=k, dwFlags=f)) for k, f in keys])
    user32.SendInput(len(keys), inputs, ctypes.sizeof(_INPUT))


def send_ctrl_c() -> None:
    _chord(VK_C)


def send_ctrl_v() -> None:
    _chord(VK_V)


def focus(hwnd: int) -> bool:
    """Brings `hwnd` forward; True only if it is in front afterwards. Windows may refuse (we
    are not the foreground process), so callers must handle False.
    ponytail: no AttachThreadInput trick; add it if refusals show up in testing."""
    if not IS_WIN:
        return False
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.05)
    front = foreground()
    return bool(front and front["hwnd"] == hwnd)
