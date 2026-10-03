"""Flashcards and quizzes from one indexed file. Generated on demand and never stored: the
user exports what they want to keep."""
import csv
import io
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.core import ram
from app.core.llm import client
from app.db import sqlite_fts

log = logging.getLogger(__name__)
router = APIRouter(prefix="/study", tags=["study"])

BUDGET_CHARS = 6000  # about 1500 tokens of source: what a 2B model can follow
FRONT_MAX, BACK_MAX = 200, 400

PROMPT = """Make {count} {what} from the numbered excerpts below, using only what they say.
Reply with JSON: {{"items": [{{"front": "{front}", "back": "short answer", "src": 1}}]}}
where src is the number of the excerpt the answer comes from.

{context}"""
_KINDS = {
    "flashcards": ("flashcards", "a term or short question"),
    "quiz": ("quiz questions", "a question with one short correct answer"),
}


class StudyRequest(BaseModel):
    path: str
    kind: str = "flashcards"
    count: int = Field(8, ge=5, le=15)


class ExportRequest(BaseModel):
    items: list[dict]


def clean_items(raw, chunks: list[dict], count: int) -> list[dict]:
    """Keeps well-formed items only: drops empty and duplicate fronts, caps lengths and count,
    and attaches the source location of the excerpt each item cites (None if it cited none)."""
    out, seen = [], set()
    for it in raw if isinstance(raw, list) else []:
        if not isinstance(it, dict):
            continue
        front, back = (" ".join(str(it.get(k) or "").split()) for k in ("front", "back"))
        if not front or not back or front.lower() in seen:
            continue
        seen.add(front.lower())
        src = it.get("src")
        c = chunks[src - 1] if isinstance(src, int) and not isinstance(src, bool) and 1 <= src <= len(chunks) else None
        out.append({"front": front[:FRONT_MAX], "back": back[:BACK_MAX], "loc_kind": c["loc_kind"] if c else None, "loc_no": c["loc_no"] if c else None})
        if len(out) == count:
            break
    return out


@router.post("/generate")
def generate(req: StudyRequest):
    if req.kind not in _KINDS:
        raise HTTPException(400, "kind must be flashcards or quiz")
    conn = sqlite_fts.connect(settings.db_path)
    try:
        if not sqlite_fts.is_indexed(conn, req.path):
            raise HTTPException(404, "That file isn't in the index (it may have moved).")
        chunks, sampled = sqlite_fts.file_chunks(conn, req.path, BUDGET_CHARS)
    finally:
        conn.close()
    if not chunks:
        raise HTTPException(422, "That file has no readable text to study from.")
    if ram.is_low():
        raise HTTPException(503, "Not enough free RAM to run the model right now. Close other apps and try again.")
    what, front = _KINDS[req.kind]
    context = "\n\n".join(f"[{i}] ({c['loc_kind']} {c['loc_no']})\n{c['text']}" for i, c in enumerate(chunks, 1))
    try:
        data = client.llm_json(PROMPT.format(count=req.count, what=what, front=front, context=context), max_tokens=req.count * 90)
    except Exception as exc:
        log.warning("study generation failed: %s", exc)
        raise HTTPException(502, f"The local model didn't answer: {exc}")
    items = clean_items(data.get("items"), chunks, req.count)
    note = "This file is long, so cards come from excerpts spread across it." if sampled else ""
    return {"path": req.path, "kind": req.kind, "items": items, "sampled": sampled, "note": note}


@router.post("/export", response_class=PlainTextResponse)
def export_csv(req: ExportRequest):
    """Cards as CSV (front, back, source) for Anki's import. Takes the cards from the page, so
    nothing needs storing here."""
    buf = io.StringIO()
    w = csv.writer(buf)
    for it in req.items[:200]:
        src = f"{it.get('loc_kind')} {it.get('loc_no')}" if it.get("loc_kind") else ""
        w.writerow([str(it.get("front", ""))[:FRONT_MAX], str(it.get("back", ""))[:BACK_MAX], src])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")
