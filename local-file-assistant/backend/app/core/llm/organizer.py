import json

from app.config import settings
from app.core.llm.client import get_client
from app.models.organize import OrganizePlan

PROMPT = """You are a file organization assistant. Given the files below, propose a reorganization
plan as JSON only, matching this schema exactly:
{{"summary": "<one sentence>", "actions": [{{"op": "move"|"delete", "path": "<current path, must be one of the files below>", "to": "<new path, only for move>"}}]}}

Only propose moves/deletes that are clearly justified: duplicates, files of one type sitting in a
folder clearly meant for another type, or backup/temp files (extensions like .bak, .tmp, .old, ~).
If no changes are needed, return {{"summary": "no changes needed", "actions": []}}.
Never invent files that aren't listed below — every "path" must be copied exactly from this list.

Files:
{files}
"""


def _drop_unknown_paths(plan: OrganizePlan, known_paths: set[str]) -> OrganizePlan:
    """Drops any action referencing a path the model wasn't shown, in case it invents one
    despite the prompt's instruction."""
    valid_actions = [a for a in plan.actions if a.path in known_paths]
    plan.dropped_invalid = len(plan.actions) - len(valid_actions)
    plan.actions = valid_actions
    return plan


def propose_plan(files: list[dict], model: str | None = None) -> OrganizePlan:
    """Proposes a plan only — never touches the filesystem."""
    listing = "\n".join(f"- {f['path']} ({f.get('size', '?')} bytes)" for f in files)
    response = get_client().chat.completions.create(
        model=model or settings.ollama_model,
        messages=[{"role": "user", "content": PROMPT.format(files=listing)}],
        response_format={"type": "json_object"},
        max_tokens=1000,  # bounds worst-case latency; JSON-grammar decoding has no natural stop otherwise
    )
    plan = OrganizePlan.model_validate(json.loads(response.choices[0].message.content))
    return _drop_unknown_paths(plan, {f["path"] for f in files})


def _demo():
    from app.models.organize import OrganizeAction

    plan = OrganizePlan(
        summary="test",
        actions=[
            OrganizeAction(op="delete", path="a.tmp"),
            OrganizeAction(op="move", path="fake_invented_file.pdf", to="somewhere.pdf"),
        ],
    )
    filtered = _drop_unknown_paths(plan, known_paths={"a.tmp", "b.docx"})
    assert filtered.dropped_invalid == 1
    assert len(filtered.actions) == 1 and filtered.actions[0].path == "a.tmp"
    print("organizer self-check OK")


if __name__ == "__main__":
    _demo()
