"""The tools the assistant may run. A tool is registered with its argument shape, a validator
(the safety check, run again at approval time), a run function and, if the effect can be
reversed, an undo. Nothing outside this registry can be proposed."""
from dataclasses import dataclass
from typing import Callable

MAX_TEXT = 100_000


class ToolError(ValueError):
    """A proposal or run was refused; the message is shown to the user."""


@dataclass(frozen=True)
class Tool:
    name: str
    risk: str  # "read": opens something. "reversible": changes something and can be undone.
    required: dict  # argument name -> type
    optional: dict
    validate: Callable[[dict], dict]  # returns normalised args, or raises ToolError
    describe: Callable[[dict], list[str]]  # the exact lines the confirm card shows
    run: Callable[[dict], dict]
    undo: Callable[[dict], dict] | None = None


REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    REGISTRY[tool.name] = tool
    return tool


def get(name: str) -> Tool:
    try:
        return REGISTRY[name]
    except KeyError:
        raise ToolError(f"Unknown action: {name!r}") from None


def validate_call(name: str, args) -> dict:
    """Shape check (names, types, sizes), then the tool's own validator."""
    tool = get(name)
    if not isinstance(args, dict):
        raise ToolError("Arguments must be an object.")
    allowed = {**tool.required, **tool.optional}
    extra = set(args) - set(allowed)
    if extra:
        raise ToolError(f"Unexpected arguments: {', '.join(sorted(extra))}")
    for key in tool.required:
        if key not in args:
            raise ToolError(f"Missing argument: {key}")
    for key, value in args.items():
        if not isinstance(value, allowed[key]):
            raise ToolError(f"Argument {key} must be {allowed[key].__name__}.")
        if isinstance(value, str) and len(value) > MAX_TEXT:
            raise ToolError(f"Argument {key} is too long.")
    return tool.validate(args)
