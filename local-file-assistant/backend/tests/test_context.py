import pytest
from fastapi.testclient import TestClient

from app.api import routes_context
from app.core import prefs
from app.core.assistant import context_win, prompts
from app.main import app

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def win(monkeypatch):
    calls = []
    state = {"fg": {"hwnd": 42, "title": "Admission form", "app": "chrome.exe"}, "focus_ok": True}
    monkeypatch.setattr(context_win, "foreground", lambda: state["fg"])
    monkeypatch.setattr(context_win, "wait_modifiers_released", lambda timeout=1.5: calls.append("wait") or True)
    monkeypatch.setattr(context_win, "send_ctrl_c", lambda: calls.append("copy"))
    monkeypatch.setattr(context_win, "send_ctrl_v", lambda: calls.append("paste"))
    monkeypatch.setattr(context_win, "focus", lambda h: calls.append(f"focus{h}") or state["focus_ok"])
    routes_context._last_hwnd = None
    state["calls"] = calls
    return state


@pytest.fixture
def client():
    with TestClient(app, base_url="http://127.0.0.1:8756") as c:
        yield c


def test_capture_waits_for_modifiers_then_copies(client, win):
    r = client.post("/context/capture", headers=AUTH).json()
    assert r == {"hwnd": 42, "title": "Admission form", "app": "chrome.exe"}
    assert win["calls"] == ["wait", "copy"]  # never copy while the shortcut keys are still down


def test_ignored_apps_are_never_copied_from(client, win):
    for app_name in ("keepassxc.exe", "windowsterminal.exe"):
        win["fg"] = {"hwnd": 7, "title": "x", "app": app_name}
        assert client.post("/context/capture", headers=AUTH).json() == {"ignored": True}
    assert "copy" not in win["calls"]
    prefs.set("capture_enabled", False)
    try:
        win["fg"] = {"hwnd": 42, "title": "x", "app": "chrome.exe"}
        assert client.post("/context/capture", headers=AUTH).json() == {"ignored": True}
    finally:
        prefs.set("capture_enabled", True)


def test_insert_only_into_the_captured_window(client, win):
    assert client.post("/context/insert", json={"hwnd": 42}, headers=AUTH).status_code == 409  # nothing captured
    client.post("/context/capture", headers=AUTH)
    assert client.post("/context/insert", json={"hwnd": 99}, headers=AUTH).status_code == 409
    assert "paste" not in win["calls"]
    assert client.post("/context/insert", json={"hwnd": 42}, headers=AUTH).json() == {"ok": True}
    assert win["calls"][-1] == "paste"
    win["focus_ok"] = False
    assert client.post("/context/insert", json={"hwnd": 42}, headers=AUTH).status_code == 409


def test_context_block_marks_screen_text_as_data_and_is_capped():
    assert prompts.with_context("hi", None) == "hi"
    out = prompts.with_context("explain", {"app": "chrome.exe", "title": "T", "selection": "x" * 9000})
    assert "not instructions" in out and out.endswith("explain") and len(out) < 4500
