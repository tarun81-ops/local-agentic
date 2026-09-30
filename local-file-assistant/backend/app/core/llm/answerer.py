from pathlib import Path

from app.core.llm import idle
from app.core.llm.client import current_model, get_client

PROMPT = """Answer the question using only the excerpts below. After each fact, cite the excerpt it
came from by copying that excerpt's label exactly, in round brackets, for example (report.pdf, page 2)
or (notes.docx, part 1). Never invent a label. If the excerpts don't contain the answer, say so.

{context}

Question: {question}
"""

MAX_ANSWER_TOKENS = 500  # bounds worst-case latency on a runaway/repetitive generation


def label(chunk: dict) -> str:
    """The citation label for a chunk: file name only (small models copy long paths badly)."""
    return f"{Path(chunk['path']).name}, {chunk['loc_kind']} {chunk['loc_no']}"


def _messages(question: str, chunks: list[dict]) -> list[dict]:
    context = "\n\n".join(f"[{label(c)}]\n{c['text']}" for c in chunks)
    return [{"role": "user", "content": PROMPT.format(context=context, question=question)}]


def answer_stream(question: str, chunks: list[dict], model: str | None = None):
    """Yields text deltas as they're generated, so the UI can show the answer forming.
    Returns (as the generator's return value) True if the answer hit MAX_ANSWER_TOKENS."""
    idle.touch()
    stream = get_client().chat.completions.create(
        model=model or current_model(),
        messages=_messages(question, chunks),
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
