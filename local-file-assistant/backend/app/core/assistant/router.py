"""Decides whether a message is about the user's files or just a chat.

Keyword search drops stopwords and ORs the rest, so "got results" means little. Auto mode
therefore needs either an explicit file word or a hit that keyword AND semantic search both
found. ponytail: heuristic; calibrate (or use reranker scores) with eval/run_eval.py."""
import re

_FILE_WORDS = re.compile(
    r"\b(files?|documents?|docs?|pdfs?|docx|folders?|notes?|invoices?|receipts?|spreadsheets?|resume|contract|my (?:files|documents|notes))\b",
    re.I,
)


def route(mode: str, message: str, results: list[dict] | None = None) -> str:
    """mode: "files" | "chat" | "auto". results: search hits (auto only). Returns "files" or "chat"."""
    if mode in ("files", "chat"):
        return mode
    if not results:
        return "chat"
    if _FILE_WORDS.search(message) or any(r.get("match") == "both" for r in results):
        return "files"
    return "chat"


_PRONOUN = re.compile(r"\b(it|its|that|this|those|these|they|them|he|she|one|ones|same|also|too)\b", re.I)


def needs_rewrite(message: str) -> bool:
    """A short follow-up ("and the second one?") can't be searched on its own."""
    return len(message.split()) <= 6 or bool(_PRONOUN.match(message.strip()))


_REMEMBER = re.compile(r"^\s*(?:please\s+)?remember(?:\s+that)?\s+(.+?)\s*$", re.I | re.S)
_FORGET = re.compile(r"^\s*(?:please\s+)?forget(?:\s+(?:that|about))?\s+(.+?)\s*$", re.I | re.S)


def memory_command(message: str) -> tuple[str, str] | None:
    """("remember" | "forget", text) for "remember that ..." / "forget ...", else None."""
    for kind, rx in (("remember", _REMEMBER), ("forget", _FORGET)):
        m = rx.match(message)
        if m:
            return kind, m.group(1).rstrip(". ")
    return None


_TASK = re.compile(
    r"^\s*(?:please\s+)?(?:(?:can|could) you\s+)?(?:remind me\b|set (?:a |an )?reminder\b|(?:add|create|new) (?:a |an )?(?:task|todo|reminder)\b|todo\s*:|task\s*:)", re.I
)
_EVENT = re.compile(r"^\s*(?:please\s+)?(?:(?:add|create|new) (?:an? )?(?:event|meeting|appointment)\b|schedule\b|book (?:a|an)\b)", re.I)


def task_intent(message: str) -> str | None:
    """"task" for reminder/to-do phrasing, "event" for scheduling a meeting, else None."""
    if _TASK.match(message):
        return "task"
    return "event" if _EVENT.match(message) else None
