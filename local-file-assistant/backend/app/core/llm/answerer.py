from pathlib import Path

from app.core.assistant import prompts
from app.core.llm import idle
from app.core.llm.client import current_model, get_client

PROMPT = """Answer the question using only the excerpts below. After each fact, cite the excerpt it
came from by copying that excerpt's label exactly, in round brackets, for example (report.pdf, page 2)
or (notes.docx, part 1). Never invent a label. If the excerpts don't contain the answer, say so.

{context}

Question: {question}
{style}"""

MAX_ANSWER_TOKENS = 500  # bounds worst-case latency on a runaway/repetitive generation


def label(chunk: dict) -> str:
    """The citation label for a chunk: file name only (small models copy long paths badly)."""
    return f"{Path(chunk['path']).name}, {chunk['loc_kind']} {chunk['loc_no']}"


def _messages(question: str, chunks: list[dict], history: list[dict] | None = None, style: str = "") -> list[dict]:
    context = "\n\n".join(f"[{label(c)}]\n{c['text']}" for c in chunks)
    return [*(history or []), {"role": "user", "content": PROMPT.format(context=context, question=question, style=style)}]


def _stream(messages: list[dict], model: str | None):
    """Yields text deltas; returns (as the generator's return value) True if cut off at the token cap."""
    idle.touch()
    stream = get_client().chat.completions.create(
        model=model or current_model(),
        messages=messages,
        max_tokens=MAX_ANSWER_TOKENS,
        stream=True,
    )
    truncated = False
    for chunk in stream:
        if not chunk.choices:
            continue
        if chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
        truncated = truncated or chunk.choices[0].finish_reason == "length"
    idle.touch()
    return truncated


def answer_stream(question: str, chunks: list[dict], history: list[dict] | None = None, model: str | None = None, style: str = ""):
    """File mode: answers from the excerpts only. Same delta/return convention as _stream.
    `style` is a ready-made prompts.style_instruction line; empty keeps the old prompt."""
    return (yield from _stream(_messages(question, chunks, history, style), model))


def chat_stream(message: str, history: list[dict] | None = None, model: str | None = None, style: str = ""):
    """General chat: no excerpts, no citations."""
    system = f"{prompts.CHAT_BASE} {style}" if style else prompts.CHAT_SYSTEM
    return (yield from _stream([{"role": "system", "content": system}, *(history or []), {"role": "user", "content": message}], model))


def rewrite_query(message: str, history: list[dict], model: str | None = None) -> str:
    """Turns a follow-up into a standalone search query. Falls back to the last question + message."""
    last_q = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    try:
        idle.touch()
        text = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in history[-4:])
        r = get_client().chat.completions.create(
            model=model or current_model(),
            messages=[{"role": "user", "content": prompts.REWRITE.format(history=text, message=message)}],
            max_tokens=40,
        )
        return (r.choices[0].message.content or "").strip().strip('"') or f"{last_q} {message}".strip()
    except Exception:
        return f"{last_q} {message}".strip()
