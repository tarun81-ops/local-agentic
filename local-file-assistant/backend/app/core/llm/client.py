import json
import re

import httpx
from openai import OpenAI

from app.config import settings
from app.core import prefs


_THINK = re.compile(r"<think>.*?(?:</think>|$)", re.S)


def strip_think(text: str) -> str:
    """Removes a <think>...</think> block (thinking models such as qwen3 / deepseek-r1 picked in Settings put it in
    the reply). An unclosed block drops everything after it."""
    return _THINK.sub("", text or "").strip()


class ThinkFilter:
    """The same for a token stream: drops text between <think> and </think>, also when a tag arrives split
    across deltas. feed() returns the text that is safe to show now; flush() the rest at the end."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self.buf, self.inside, self.started = "", False, False

    @staticmethod
    def _partial(buf: str, tag: str) -> int:
        """Length of the longest end of buf that could be the start of tag."""
        return next((k for k in range(min(len(tag) - 1, len(buf)), 0, -1) if buf.endswith(tag[:k])), 0)

    def feed(self, text: str) -> str:
        self.buf += text
        out = ""
        while True:
            if self.inside:
                i = self.buf.find(self.CLOSE)
                if i < 0:
                    self.buf = self.buf[-(len(self.CLOSE) - 1):]
                    break
                self.buf, self.inside = self.buf[i + len(self.CLOSE):].lstrip(), False
                continue
            i = self.buf.find(self.OPEN)
            if i >= 0:
                out, self.buf, self.inside = out + self.buf[:i], self.buf[i + len(self.OPEN):], True
                continue
            k = self._partial(self.buf, self.OPEN)
            out, self.buf = out + self.buf[: len(self.buf) - k], self.buf[len(self.buf) - k:]
            break
        if not self.started:  # no leading blank line where a think block was
            out = out.lstrip()
            self.started = bool(out)
        return out

    def flush(self) -> str:
        rest = "" if self.inside else self.buf
        self.buf = ""
        return rest


def parse_json_object(text: str) -> dict:
    """The first JSON object in a model reply, also when a small model wraps it in ```json fences, puts <think>
    text first or adds a sentence around it. {} if there is none."""
    text = strip_think(text)
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except ValueError:
        pass
    start = text.find("{")
    while start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(text[start : i + 1])
                        if isinstance(data, dict):
                            return data
                    except ValueError:
                        pass
                    break
        start = text.find("{", start + 1)
    return {}


def get_client(local: bool = False) -> OpenAI:
    """Ollama's OpenAI-compatible endpoint, or the other OpenAI-compatible server switched on in Settings > Model
    (core/llm/external.py). local=True is always Ollama: image captions, the briefing and the planners must never leave
    this PC, whatever the other server setting says."""
    from app.core.llm import external

    ext = None if local else external.active()
    if ext:
        return OpenAI(base_url=ext["url"], api_key="unused", timeout=httpx.Timeout(settings.llm_timeout_s, connect=5), max_retries=0)
    # No retries: a second attempt doubles the wait, and the caller reports the failure anyway.
    return OpenAI(
        base_url=settings.ollama_base_url,
        api_key="ollama",
        timeout=httpx.Timeout(settings.llm_timeout_s, connect=5),
        max_retries=0,
    )


def current_model(local: bool = False) -> str:
    """The model picked in Settings (saved in the data dir), else OLLAMA_MODEL; local=True ignores the other server."""
    from app.core.llm import external

    ext = None if local else external.active()
    return ext["model"] if ext else prefs.get("model") or settings.ollama_model


def set_model(name: str) -> None:
    prefs.set("model", name)


def llm_json(prompt: str, max_tokens: int = 300, local: bool = True) -> dict:
    """One non-streamed call that must answer with a JSON object; {} if it doesn't. local=True (the default) keeps it
    on this PC: the file pickers and the tool planner send file names and the tool list, which only the user's
    questions and flashcards (local=False) may take to the other server. A planner whose local engine is down fails
    (the caller skips its plan) instead of falling back to the other server."""
    from app.core.llm import idle

    idle.touch()
    r = get_client(local=local).chat.completions.create(
        model=current_model(local=local),
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    return parse_json_object(r.choices[0].message.content or "")


def ollama_status() -> dict:
    """Whether the model server answers, and which models it has. (Named for Ollama, but
    works for any OpenAI-compatible server when llm_provider is "openai".)"""
    try:
        if settings.llm_provider != "ollama":
            r = httpx.get(f"{settings.ollama_base_url.rstrip('/')}/models", timeout=3)
            r.raise_for_status()
            return {"connected": True, "models": sorted(m["id"] for m in r.json().get("data", []))}
        r = httpx.get(f"{settings.ollama_url}/api/tags", timeout=3)
        r.raise_for_status()
        return {"connected": True, "models": sorted(m["name"] for m in r.json().get("models", []))}
    except Exception as exc:
        return {"connected": False, "models": [], "error": str(exc)}
