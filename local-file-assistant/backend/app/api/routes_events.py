import queue

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.core.assistant.scheduler import bus, sse

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/stream")
def stream():
    """Server-sent events for Electron's main process: reminders (and later, briefings)."""
    q = bus.subscribe()

    def gen():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    yield sse(q.get(timeout=15))
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")
