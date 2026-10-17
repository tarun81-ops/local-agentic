import json
import re
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core import pc_access, personalize, ram
from datetime import datetime

from app.core.assistant import actions, chat_recall, conversation, file_agent, memory_store, prompts, tasks
from app.core.assistant.tools import intents
from app.core.assistant.tools.registry import ToolError
from app.core import calc, facts, find_file
from app.core.assistant.router import memory_command, organize_intent, needs_rewrite, route, task_intent
from app.core.llm.answerer import answer_stream, chat_stream, rewrite_query
from app.core.llm.verifier import STRICT_THRESHOLD, THRESHOLD, excerpt, verify
from app.core.search.hybrid import hybrid_search
from app.db import sqlite_fts
from app.config import settings
from pathlib import Path

log = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    conversation_id: int | None = None
    mode: str = "auto"  # "auto" | "files" | "chat"
    root: str | None = None
    model: str | None = None
    style: str | None = None  # one-question override of the saved answer style
    language: str | None = None  # same, for the reply language
    context: dict | None = None  # {app, title, selection} captured from the window in front


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _public(chunk: dict) -> dict:
    return {k: chunk[k] for k in ("path", "name", "loc_kind", "loc_no", "snippet", "match")}


def _pc_lookup(query: str, message: str, kind: str, chunks: list[dict]):
    """Whole-PC access: when it is on and the question is about a document, find files by name across the
    enabled drives and read the best ones live. Yields progress events and returns (chunks, kind). Never
    raises: a failure here leaves the normal search results as they were."""
    if not pc_access.enabled() or not (kind == "files" or file_agent.agent_worthy(message)):
        return chunks, kind
    gen = file_agent.gather(query, content_hits=chunks)
    try:
        while True:
            try:
                yield _sse("pc_step", next(gen))
            except StopIteration as stop:
                found = stop.value or {"chunks": []}
                break
    except Exception:
        log.exception("PC file agent failed")
        return chunks, kind
    have = {(c["path"], c["loc_kind"], int(c["loc_no"])) for c in chunks}
    extra = [c for c in found["chunks"] if (c["path"], c["loc_kind"], int(c["loc_no"])) not in have]
    return (chunks + extra, "files") if extra else (chunks, kind)


def _title_chunks(path: str, message: str) -> list[dict]:
    """Passages of one file found by its name: from the index when it is indexed, else read live (PC access)."""
    from app.core.search.hybrid import snippet

    terms = [w for w in find_file.fuzzy.title_words(message)]
    conn = sqlite_fts.connect(settings.db_path)
    try:
        rows, _ = sqlite_fts.file_chunks(conn, path, budget=6000)
    finally:
        conn.close()
    if rows:
        return [{"path": path, "name": Path(path).name, "loc_kind": r["loc_kind"], "loc_no": r["loc_no"], "chunk_no": i,
                 "text": r["text"], "snippet": snippet(r["text"], terms), "match": "name"} for i, r in enumerate(rows)]
    got, _problem = file_agent.read_chunks(path, message, terms)
    for c in got:
        c["match"] = "name"
    return got


def _title_lookup(message: str, kind: str, chunks: list[dict]):
    """Nothing in the files matched the words, but the message may be a file's title typed by sound
    ("gunaho ke devata" for "Gunahon Ka Devta.pdf"). Yields progress; returns (chunks, kind). Never raises."""
    try:
        hits = find_file.title_hits(message)
        if not hits:
            return chunks, kind
        found: list[dict] = []
        for h in hits:
            got = _title_chunks(h["path"], message)
            if got:
                found.extend(got)
                yield _sse("pc_step", {"step": "name", "detail": f"Matched by file name: {h['name']}"})
                break  # one file: the title named one book or document
        return (found, "files") if found else (chunks, kind)
    except Exception:
        log.exception("title lookup failed")
        return chunks, kind


def _marks_reply(message: str, chunks: list[dict]) -> str | None:
    """Percentage / total questions about a marksheet: the sums are done in code (app.core.calc), not by the model.
    None when no marksheet among the results could be read, so the normal answer path runs."""
    by_file: dict[str, list[dict]] = {}
    for c in chunks:
        by_file.setdefault(c["path"], []).append(c)
    want = re.search(r"(?:class|std|standard)\s*(\d{1,2})|(\d{1,2})\s*(?:th|st|nd|rd)", message, re.I)
    num = (want.group(1) or want.group(2)) if want else None
    paths = [p for p in by_file if not num or num in Path(p).name] or list(by_file)
    out = []
    for path in paths:
        group = sorted(by_file[path], key=lambda c: (int(c["loc_no"]), c.get("chunk_no", 0)))
        text = " ".join(c["text"] for c in group)
        part = calc.answer_for(Path(path).name, int(group[0]["loc_no"]), text)
        if part:
            out.append(part)
        if len(out) == 2:
            break
    return "\n\n".join(out) if out else None



def _direct_reply(message: str, chunks: list[dict]) -> str | None:
    """Questions answered in code instead of by the model: marks arithmetic, IFSC codes. None = use the model."""
    if calc.wanted(message):
        reply = _marks_reply(message, chunks)
        if reply:
            return reply
    if facts.asks_ifsc(message):
        return facts.ifsc_reply(message, chunks)
    return None


def _generate(deltas):
    """Drives an answer generator: yields ("token", delta) pairs, then ("end", truncated)."""
    while True:
        try:
            delta = next(deltas)
        except StopIteration as stop:  # the stream's return value: cut off at the token cap?
            yield "end", bool(stop.value)
            return
        yield "token", delta


def _recall(message: str) -> list[dict]:
    try:
        return memory_store.recall(message)
    except Exception:  # memory is a bonus: a failure must never block an answer
        log.exception("memory recall failed")
        return []


def _recall_chats(message: str, conv: dict | None) -> list[dict]:
    """Relevant exchanges from earlier conversations (never the current one). Same rule as memory:
    a failure here must never block an answer."""
    try:
        return chat_recall.recall(message, exclude_conv=conv["id"] if conv else None)
    except Exception:
        log.exception("chat recall failed")
        return []


def _folder_style(root: str | None, chunks: list[dict]) -> str:
    """The answer style of the folder being asked about (the request's root, else the top result's
    folder), or "" when it has none. Never lets a lookup problem block an answer."""
    try:
        conn = sqlite_fts.connect(settings.db_path)
        try:
            found = root or (sqlite_fts.root_for(conn, Path(chunks[0]["path"])) if chunks else None)
        finally:
            conn.close()
        return personalize.folder_profile(found).get("answer_style", "") if found else ""
    except Exception:
        log.exception("folder style lookup failed")
        return ""


def _memory_reply(kind: str, text: str) -> str:
    if kind == "remember":
        return f'Remembered: "{text}".' if memory_store.add(text) else "I already remember that."
    gone = memory_store.forget(text)
    return f'Forgot: "{gone}".' if gone else "I don't have a memory matching that."


def _proposal_text(p: dict) -> str:
    what = "event" if p["kind"] == "event" else "reminder" if p["when"] else "task"
    if p["when"] is None:
        return f"I can add that {what}: \u201c{p['title']}\u201d, with no time yet. Confirm or set a time below."
    at = datetime.fromtimestamp(p["when"]).strftime("%a %d %b" if p["all_day"] else "%a %d %b, %I:%M %p")
    note = " I assumed 9:00 as no time was given; check it." if p["confidence"] == "date_only" and not p["all_day"] else ""
    return f"I can set that {what}: \u201c{p['title']}\u201d, {at}.{note} Confirm or edit it below."


def _action_reply(req: ChatRequest, conv: dict | None, act: dict):
    """Stores a validated proposal and shows its card. Nothing runs until the user approves it."""
    yield _sse("route", {"route": "action"})
    try:
        proposal = actions.propose(act["tool"], act["args"], conv["id"] if conv else None)
        reply = "I can do that. Check the exact details below, then approve."
        yield _sse("tool_proposal", proposal)
    except ToolError as exc:
        reply = f"I can't do that: {exc}"
    yield _sse("token", {"delta": reply})
    yield from _finish(req, conv, {"citations": [], "uncited": [], "all_verified": False, "answer": reply, "truncated": False, "route": "action"})


def _stream(req: ChatRequest, conv: dict | None):
    act = None if memory_command(req.message) or task_intent(req.message) else intents.from_message(req.message)
    if act:  # from the user's own words only; model output and file text are never turned into actions
        yield from _action_reply(req, conv, act)
        return
    intent = task_intent(req.message)
    if intent and not memory_command(req.message):  # a reminder or event: propose it, save only on confirm
        proposal = tasks.parse_message(req.message, intent)
        reply = _proposal_text(proposal)
        yield _sse("route", {"route": "task"})
        yield _sse("action", {"proposal": proposal})
        yield _sse("token", {"delta": reply})
        yield from _finish(req, conv, {"citations": [], "uncited": [], "all_verified": False, "answer": reply, "truncated": False, "route": "task"})
        return
    cmd = memory_command(req.message)
    if cmd:  # "remember that ..." / "forget ...": handled here, no model involved
        reply = _memory_reply(*cmd)
        yield _sse("route", {"route": "memory"})
        yield _sse("token", {"delta": reply})
        yield from _finish(req, conv, {"citations": [], "uncited": [], "all_verified": False, "answer": reply, "truncated": False, "route": "memory"})
        return
    if conv and req.mode != "chat" and facts.asks_source(req.message):
        src = facts.source_reply(conv["messages"])
        if src:  # "which file was that from?": the file the last answer used, not a fresh search
            text, cits = src
            yield _sse("route", {"route": "files"})
            yield _sse("token", {"delta": text})
            yield from _finish(req, conv, {"citations": cits, "uncited": [], "all_verified": all(c.get("verified") for c in cits),
                                           "answer": text, "truncated": False, "route": "files"})
            return
    history = conversation.window(conv["messages"]) if conv else []
    chunks: list[dict] = []
    kind = req.mode if req.mode in ("files", "chat") else "auto"
    if kind == "auto" and organize_intent(req.message):
        kind = "chat"  # clean-up request: the role points to Tidy-up; a file search would only find noise

    if kind != "chat":
        # Search resolves before any LLM call, so the UI shows matching files straight away
        # even while the model is still loading.
        query = rewrite_query(req.message, history, req.model) if history and needs_rewrite(req.message) else req.message
        try:
            found = hybrid_search(query, root=req.root, limit=6)
        except Exception as exc:  # e.g. a locked or damaged index: say so, don't leave "Searching…" up
            log.exception("search failed")
            yield _sse("error", {"detail": f"Couldn't search your files: {exc}"})
            return
        chunks = found["results"]
        kind = route(kind, req.message, chunks)
        yield _sse("route", {"route": kind})
        if kind == "files":
            yield _sse("results", {"results": [_public(c) for c in chunks], "semantic": found["semantic"]})
        before, was = len(chunks), kind
        chunks, kind = yield from _pc_lookup(query, req.message, kind, chunks)
        if kind != was:
            yield _sse("route", {"route": kind})
        if len(chunks) != before:
            yield _sse("results", {"results": [_public(c) for c in chunks], "semantic": found["semantic"]})
        if kind == "chat" or not chunks:
            was = kind
            chunks, kind = yield from _title_lookup(req.message, kind, chunks)
            if kind != was:
                yield _sse("route", {"route": kind})
            if kind == "files" and chunks:
                yield _sse("results", {"results": [_public(c) for c in chunks], "semantic": found["semantic"]})
    else:
        yield _sse("route", {"route": "chat"})

    if kind == "files" and not chunks:
        done = {"citations": [], "uncited": [], "all_verified": False, "answer": "", "no_results": True, "truncated": False, "route": kind}
        yield from _finish(req, conv, done)
        return

    if kind == "files":
        try:
            reply = _direct_reply(req.message, chunks)
        except Exception:  # a parsing problem must fall back to the normal answer, never block it
            log.exception("direct answer failed")
            reply = None
        if reply:
            yield _sse("token", {"delta": reply})
            result = verify(reply, chunks, THRESHOLD)
            _attach_snippets(result, chunks)
            yield from _finish(req, conv, {**result, "answer": reply, "truncated": False, "route": kind})
            return

    # File answers come from the files only. Remembered facts mixed in turned "class 10 roll no" into
    # "The user is in Class 10" (from an old "user is in class 12" memory), so they are for chat only.
    remembered = _recall(req.message) if kind != "files" else []
    used = [{"id": m["id"], "text": m["text"]} for m in remembered]
    if used:
        yield _sse("memory_used", {"memories": used})

    past = _recall_chats(req.message, conv) if kind == "chat" or settings.recall_in_files else []
    seen = [{"conv_id": p["conv_id"], "title": p["title"], "snippet": p["text"][:200], "at": p["created_at"]} for p in past]
    if seen:
        yield _sse("recall_used", {"chats": seen})

    mem = ram.status()
    if mem["low"]:
        yield _sse("warning", {"detail": f"Only {mem['free_mb']} MB of RAM free; the answer may be slow. Closing other apps helps."})

    answer = ""
    truncated = False
    pz = personalize.get_all()
    chosen = req.style if req.style in personalize.STYLES else _folder_style(req.root, chunks) or pz["answer_style"]  # request > folder > saved default
    style = prompts.style_instruction(chosen, req.language if req.language in personalize.LANGUAGES else pz["language"], pz["cite_pages"])
    # Order: screen context, profile, memory, then the message. The search used the bare message.
    prompt = prompts.with_memory(prompts.with_recall(prompts.with_profile(prompts.with_context(req.message, req.context), pz["profile"]), past), remembered)
    deltas = answer_stream(prompt, chunks, history, model=req.model, style=style) if kind == "files" else chat_stream(prompt, history, model=req.model, style=style)
    try:
        for what, value in _generate(deltas):
            if what == "token":
                answer += value
                yield _sse("token", {"delta": value})
            else:
                truncated = value
    except Exception as exc:
        log.exception("answer generation failed")
        yield _sse("error", {"detail": f"The local model didn't answer: {exc}"})
        return

    if kind == "files":
        result = verify(answer, chunks, STRICT_THRESHOLD if pz["verifier_strict"] == "strict" else THRESHOLD)
        _attach_snippets(result, chunks)
    else:  # general answers are not checked against files
        result = {"citations": [], "uncited": [], "all_verified": False}
    yield from _finish(req, conv, {**result, "answer": answer, "truncated": truncated, "route": kind, "memory_used": used, "recall_used": seen})


def _attach_snippets(result: dict, chunks: list[dict]) -> None:
    """Each cited source gets the part of its text that backs the claim (centred on the claim's number
    or name), falling back to the search snippet when the claim's facts aren't in it."""
    by_loc: dict[tuple, dict] = {}
    for c in chunks:
        entry = by_loc.setdefault((c["path"], c["loc_kind"], int(c["loc_no"])), {"text": "", "snippet": c.get("snippet", "")})
        entry["text"] += "\n" + c.get("text", "")
    for cit in result["citations"]:
        if cit.get("path"):
            src = by_loc.get((cit["path"], cit["loc_kind"], int(cit["loc_no"])), {"text": "", "snippet": ""})
            cit["snippet"] = excerpt(src["text"], cit.get("claim", "")) or src["snippet"]


def _finish(req: ChatRequest, conv: dict | None, done: dict):
    """Saves the exchange (only complete ones, so history never holds a dangling question)."""
    meta = {k: done[k] for k in ("route", "citations", "uncited", "truncated", "no_results", "memory_used", "recall_used") if k in done}
    title = conv["title"] if conv else conversation.title_from(req.message)
    conv_id = conv["id"] if conv else conversation.create(title)["id"]
    _, answer_id = conversation.add_messages(conv_id, [("user", req.message, {}), ("assistant", done["answer"], meta)])
    yield _sse("conversation", {"id": conv_id, "title": title, "message_id": answer_id})
    yield _sse("done", done)
    chat_recall.schedule_embed()  # after the reply: embedding must never delay it


@router.post("")
def chat(req: ChatRequest):
    conv = None
    if req.conversation_id is not None:
        conv = conversation.get(req.conversation_id)
        if conv is None:
            raise HTTPException(404, "No such conversation")
    return StreamingResponse(_stream(req, conv), media_type="text/event-stream")
