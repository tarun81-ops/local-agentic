from app.config import settings
from app.core.llm.client import get_client

PROMPT = """Answer the question using only the excerpts below. Cite each fact using the excerpt's
actual filename and page number, like this: (report.pdf, page 2) — substitute the real filename
and number, never write the literal words "file" or "page N" as placeholders.
If the excerpts don't contain the answer, say so.

{context}

Question: {question}
"""


def _messages(question: str, chunks: list[dict]) -> list[dict]:
    context = "\n\n".join(f"[{c['path']}, page {c['chunk_no']}]\n{c['text']}" for c in chunks)
    return [{"role": "user", "content": PROMPT.format(context=context, question=question)}]


MAX_ANSWER_TOKENS = 500  # bounds worst-case latency on a runaway/repetitive generation


def answer(question: str, chunks: list[dict], model: str | None = None) -> str:
    response = get_client().chat.completions.create(
        model=model or settings.ollama_model,
        messages=_messages(question, chunks),
        max_tokens=MAX_ANSWER_TOKENS,
    )
    return response.choices[0].message.content


def answer_stream(question: str, chunks: list[dict], model: str | None = None):
    """Yields text deltas as they're generated, so the UI can show the answer forming instead of waiting."""
    stream = get_client().chat.completions.create(
        model=model or settings.ollama_model,
        messages=_messages(question, chunks),
        max_tokens=MAX_ANSWER_TOKENS,
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
