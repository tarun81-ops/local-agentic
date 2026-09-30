"""Proposes a reorganization plan. It never touches the filesystem.

Duplicates and backup/temp files are found deterministically (by content hash and file
name). The model is only asked what it can actually judge from what it's shown: which
folder each remaining file belongs in, given its name and the start of its text."""
import json
import re
from pathlib import Path, PurePath

from app.core.llm import idle
from app.core.file_ops.paths import name_problem
from app.core.llm.client import current_model, get_client
from app.models.organize import OrganizeAction, OrganizePlan

MAX_FILES_FOR_MODEL = 120  # keeps the prompt inside a small local model's comfortable context
JUNK_RE = re.compile(r"(\.(bak|tmp|old|temp)$)|(~$)|(^~\$)", re.IGNORECASE)

PROMPT = """You organize files into sensible folders. For each file below, choose a short folder path
(relative, like "Finance/Invoices" or "Meeting Notes/2024") that fits its name and content.
Only include files that are clearly in the wrong place; leave the rest out.
Reply with JSON only: {{"moves": [{{"path": "<exact path from the list>", "folder": "<relative folder>"}}]}}
If nothing needs moving, reply {{"moves": []}}.

Files (path — current folder — start of content):
{files}
"""


def clean_folder(folder: str) -> str | None:
    """A safe relative folder, or None: no drive letters, absolute paths or '..'."""
    folder = folder.strip().replace("\\", "/")
    if not folder or folder.startswith("/") or ":" in folder:
        return None
    parts = [p.strip() for p in PurePath(folder).parts if p.strip()]
    if not parts or any(name_problem(p) for p in parts):  # also '..', '.', and names Windows rejects
        return None
    return "/".join(parts)


def _deterministic(files: list[dict], duplicate_groups: list[list[dict]]) -> tuple[list[OrganizeAction], set[str]]:
    actions: list[OrganizeAction] = []
    handled: set[str] = set()
    for group in duplicate_groups:
        keep = group[0]  # oldest copy stays
        for dup in group[1:]:
            actions.append(
                OrganizeAction(
                    op="delete",
                    path=dup["path"],
                    reason=f"Same content as {Path(keep['path']).name}",
                    group="Duplicates",
                )
            )
            handled.add(dup["path"])
    for f in files:
        if f["path"] not in handled and JUNK_RE.search(Path(f["path"]).name):
            actions.append(OrganizeAction(op="delete", path=f["path"], reason="Backup or temp file", group="Backup & temp files"))
            handled.add(f["path"])
    return actions, handled


def _ask_model(candidates: list[dict], model: str | None) -> list[dict]:
    listing = "\n".join(
        f"- {f['path']} — {Path(f['path']).parent.name or '.'} — {(f.get('hint') or '').strip()[:140]}"
        for f in candidates
    )
    idle.touch()
    response = get_client().chat.completions.create(
        model=model or current_model(),
        messages=[{"role": "user", "content": PROMPT.format(files=listing)}],
        response_format={"type": "json_object"},
        max_tokens=1200,  # JSON-grammar decoding has no natural stop otherwise
    )
    data = json.loads(response.choices[0].message.content or "{}")
    return list(data.get("moves") or [])


def propose_plan(
    root: str,
    files: list[dict],
    duplicate_groups: list[list[dict]],
    model: str | None = None,
    ask_model=None,
) -> OrganizePlan:
    """files: [{path, size, hint}] for the chosen indexed folder."""
    ask_model = ask_model or _ask_model
    root_path = Path(root)
    actions, handled = _deterministic(files, duplicate_groups)
    known = {f["path"] for f in files}

    candidates = [f for f in files if f["path"] not in handled][:MAX_FILES_FOR_MODEL]
    dropped = 0
    if candidates:
        moves = ask_model(candidates, model)
        taken: set[str] = set()
        for m in moves:
            path, folder = str(m.get("path", "")), clean_folder(str(m.get("folder", "")))
            if path not in known or path in handled or folder is None:
                dropped += 1
                continue
            dest = root_path / folder / Path(path).name
            if dest == Path(path) or str(dest) in taken:
                continue
            taken.add(str(dest))
            handled.add(path)
            actions.append(OrganizeAction(op="move", path=path, to=str(dest), group=folder, reason="Suggested folder"))

    # Summarised from the actions themselves, never from the model: its own summary could
    # describe moves that were just dropped as invalid.
    moves_n = sum(a.op == "move" for a in actions)
    deletes_n = sum(a.op == "delete" for a in actions)
    summary = f"{moves_n} files to move, {deletes_n} to send to the Recycle Bin." if actions else "No changes needed."
    return OrganizePlan(summary=summary, root=root, actions=actions, dropped_invalid=dropped)
