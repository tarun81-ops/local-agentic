import threading

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel

from app.core.assistant.voice import VoiceUnavailable, stt, tts

router = APIRouter(prefix="/voice", tags=["voice"])

MAX_BYTES = stt.SAMPLE_RATE * 2 * stt.MAX_SECONDS
_downloading = threading.Lock()


class Speak(BaseModel):
    text: str


def _state() -> dict:
    return {"stt": {"available": stt.available(), "model": stt.model_name()}, "tts": {"available": tts.available(), "voice": tts.voice_name()}}


@router.get("/status")
def status():
    return _state()


@router.post("/stt")
async def speech_to_text(request: Request, language: str | None = None):
    """Body: raw 16 kHz mono signed 16-bit little-endian PCM. Returns the words spoken."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BYTES:
        raise HTTPException(413, "That recording is too long.")
    data = await request.body()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "That recording is too long.")
    try:
        return {"text": await run_in_threadpool(stt.transcribe, data, language)}
    except VoiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from None


@router.post("/tts")
def text_to_speech(body: Speak):
    try:
        return Response(content=tts.synthesize(body.text), media_type="audio/wav")
    except VoiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/download")
def download_models():
    """Fetches the speech and voice models (a few hundred MB, once). Slow: the page shows a spinner."""
    if not _downloading.acquire(blocking=False):
        raise HTTPException(409, "A download is already running.")
    try:
        errors = {}
        for name, fetch in (("stt", stt.download), ("tts", tts.download)):
            try:
                fetch()
            except Exception as exc:  # offline, disk full, ...: report per model
                errors[name] = str(exc)
        return {**_state(), "errors": errors}
    finally:
        _downloading.release()
