"""Pulls lasting facts about the user out of finished conversations. Runs in the background
after a chat has been quiet for extract_idle_s; the model proposes, code filters."""
import logging
import re
import threading
import time

from app.config import settings
from app.core import prefs, ram
from app.core.assistant import memory_store
from app.core.llm import client
from app.db import assistant_db

log = logging.getLogger(__name__)

PROMPT = """From the user's messages below, list lasting facts about the user worth remembering
(preferences, name, job, projects, people, habits). Skip one-off requests and anything about files'
contents. Reply with JSON: {{"facts": ["short sentence", ...]}} (empty list if none).

Messages:
{messages}"""

# Never stored automatically (only when the user says "remember ..." themselves).
BLOCKED = re.compile(
    r"\d[\d \-]{8,}\d|password|passcode|\bpin\b|\botp\b|cvv|\bssn\b|aadhaar|aadhar|passport|credit card|debit card|bank account|\biban\b"
    r"|salary|\bloan\b|diagnos|medication|prescription|disease|illness|medical|health|cancer|therapy|\bpan card\b",
    re.I,
)
_QUESTION = re.compile(r"^(what|who|when|where|why|how|which|do|does|did|is|are|can|could|would|should)\b", re.I)


def clean(facts) -> list[str]:
    """Keeps short, plain statements; drops questions and sensitive categories."""
    out = []
    for f in facts if isinstance(facts, list) else []:
        if not isinstance(f, str):
            continue
        f = " ".join(f.split())
        if 8 <= len(f) <= 200 and not f.endswith("?") and not _QUESTION.match(f) and not BLOCKED.search(f):
            out.append(f)
    return out


def _pending(idle_s: float, now: float) -> list[tuple[int, int]]:
    """(conversation id, extracted_msg_id) for chats that went quiet with unread messages."""
    conn = assistant_db.connect()
    try:
        rows = conn.execute(
            "SELECT c.id, c.extracted_msg_id FROM conversations c JOIN messages m ON m.conv_id = c.id "
            "GROUP BY c.id HAVING MAX(m.id) > c.extracted_msg_id AND MAX(m.created_at) < ?",
            (now - idle_s,),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]
    finally:
        conn.close()


def extract_conversation(conv_id: int, after_id: int) -> int:
    """Looks at the user's new messages; returns how many facts were saved."""
    conn = assistant_db.connect()
    try:
        rows = conn.execute("SELECT id, content FROM messages WHERE conv_id = ? AND id > ? AND role = 'user' ORDER BY id", (conv_id, after_id)).fetchall()
        last = conn.execute("SELECT MAX(id) FROM messages WHERE conv_id = ?", (conv_id,)).fetchone()[0]
    finally:
        conn.close()
    saved = 0
    if rows:
        text = "\n".join(f"- {r['content'][:500]}" for r in rows[-20:])
        for fact in clean(client.llm_json(PROMPT.format(messages=text), max_tokens=300).get("facts")):
            if memory_store.add(fact, source_conv=conv_id, source_msg=rows[-1]["id"]):
                saved += 1
    conn = assistant_db.connect()
    try:
        conn.execute("UPDATE conversations SET extracted_msg_id = ? WHERE id = ?", (last, conv_id))
        conn.commit()
    finally:
        conn.close()
    return saved


def run_once(now: float | None = None) -> int:
    if not prefs.get("memory_extract", True) or ram.is_low():
        return 0
    total = 0
    for conv_id, after in _pending(settings.extract_idle_s, now or time.time()):
        try:
            total += extract_conversation(conv_id, after)
        except Exception as exc:  # model down: try again on the next pass
            log.warning("memory extraction skipped: %s", exc)
            break
    return total


_stop = threading.Event()


def _loop() -> None:
    while not _stop.wait(60):
        run_once()


def start() -> None:
    _stop.clear()
    threading.Thread(target=_loop, name="memory-extract", daemon=True).start()


def stop() -> None:
    _stop.set()
