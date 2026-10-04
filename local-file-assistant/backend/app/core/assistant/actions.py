"""Proposed actions and their audit trail (tool_runs). The life of one action:
proposed -> (approve) -> running -> done | failed, or proposed -> rejected; done -> undone.
Arguments are validated when proposed and again when approved, and an action runs at most once."""
import json
import time

from app.core.assistant.tools import builtin  # noqa: F401 - registers the tools
from app.core.assistant.tools import registry
from app.core.assistant.tools.registry import ToolError
from app.db import assistant_db


class NotFound(LookupError):
    pass


class Conflict(ToolError):
    """The action isn't in a state that allows this (for example, already approved)."""


def _row(r) -> dict:
    d = dict(r)
    d["args"] = json.loads(d.pop("args_json"))
    d["result"] = json.loads(d.pop("result_json"))
    return d


def get(rid: int) -> dict:
    conn = assistant_db.connect()
    try:
        r = conn.execute("SELECT * FROM tool_runs WHERE id = ?", (rid,)).fetchone()
    finally:
        conn.close()
    if r is None:
        raise NotFound(rid)
    return _row(r)


def _claim(rid: int, frm: str, to: str) -> dict:
    """Atomically moves the action from one status to another: of two concurrent approvals only one wins."""
    conn = assistant_db.connect()
    try:
        won = conn.execute("UPDATE tool_runs SET status = ? WHERE id = ? AND status = ?", (to, rid, frm)).rowcount
        conn.commit()
    finally:
        conn.close()
    row = get(rid)
    if not won:
        raise Conflict(f"That action is already {row['status']}.")
    return row


def _finish(rid: int, status: str, result: dict | None = None) -> None:
    conn = assistant_db.connect()
    try:
        if result is None:
            conn.execute("UPDATE tool_runs SET status = ? WHERE id = ?", (status, rid))
        else:
            conn.execute("UPDATE tool_runs SET status = ?, result_json = ? WHERE id = ?", (status, json.dumps(result), rid))
        conn.commit()
    finally:
        conn.close()


def propose(name: str, args: dict, conv_id: int | None = None) -> dict:
    """Validates and stores an action; nothing runs. Returns what the confirm card shows."""
    tool = registry.get(name)
    clean = registry.validate_call(name, args)
    conn = assistant_db.connect()
    try:
        rid = conn.execute(
            "INSERT INTO tool_runs(conv_id, tool, args_json, risk, created_at) VALUES (?, ?, ?, ?, ?)",
            (conv_id, name, json.dumps(clean), tool.risk, time.time()),
        ).lastrowid
        conn.commit()
    finally:
        conn.close()
    return {"id": rid, "tool": name, "risk": tool.risk, "args": clean, "lines": tool.describe(clean), "undoable": tool.undo is not None}


def approve(rid: int) -> dict:
    row = _claim(rid, "proposed", "running")
    tool = registry.get(row["tool"])
    try:
        args = registry.validate_call(row["tool"], row["args"])  # the world may have changed since the proposal
        result = tool.run(args)
    except Exception as exc:
        _finish(rid, "failed", {"error": str(exc)})
        raise (exc if isinstance(exc, ToolError) else ToolError(str(exc))) from exc
    _finish(rid, "done", result)
    return get(rid)


def reject(rid: int) -> dict:
    _claim(rid, "proposed", "rejected")
    return get(rid)


def undo(rid: int) -> dict:
    tool = registry.get(get(rid)["tool"])
    if tool.undo is None:
        raise ToolError("That action can't be undone.")
    row = _claim(rid, "done", "undoing")
    try:
        out = tool.undo(row["result"])
    except Exception as exc:
        _finish(rid, "done")  # still done; the undo didn't happen
        raise (exc if isinstance(exc, ToolError) else ToolError(str(exc))) from exc
    _finish(rid, "undone", {**row["result"], "undo": out})
    return get(rid)


def history(conv_id: int | None = None, limit: int = 50) -> list[dict]:
    where, args = ("WHERE conv_id = ?", (conv_id,)) if conv_id is not None else ("", ())
    conn = assistant_db.connect()
    try:
        return [_row(r) for r in conn.execute(f"SELECT * FROM tool_runs {where} ORDER BY id DESC LIMIT ?", (*args, limit))]
    finally:
        conn.close()
