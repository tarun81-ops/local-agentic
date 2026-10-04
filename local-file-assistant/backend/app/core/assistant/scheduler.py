"""Fires due reminders. A thread wakes every 30 s, finds open tasks whose reminder time has
passed and publishes them on an in-process bus; /events/stream relays the bus to Electron,
which shows the desktop notification. A reminder only counts as fired once someone is listening,
so ones due while the app was closed arrive (flagged "missed") when it reconnects."""
import json
import logging
import queue
import threading
import time
from datetime import datetime

from app.core.assistant import briefing, tasks

log = logging.getLogger(__name__)

MISSED_AFTER_S = 300  # reminders this late are announced as missed, not as due now


class Bus:
    def __init__(self) -> None:
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event: dict) -> int:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            q.put(event)
        return len(subs)


bus = Bus()


def tick(now: float | None = None) -> int:
    """Publishes every due reminder; returns how many fired."""
    now = now or time.time()
    fired = []
    for t in tasks.due_reminders(now):
        event = {"type": "reminder", "missed": now - t["remind_at"] > MISSED_AFTER_S, "task": {k: t[k] for k in ("id", "title", "due_at", "remind_at", "file_path")}}
        if bus.publish(event) == 0:
            break  # nobody is listening yet: leave the rest for the next tick
        fired.append(t["id"])
    tasks.mark_notified(fired, now)
    return len(fired)


def nudge_tick(now: datetime | None = None) -> int:
    """Sends the briefing/nudges that are due (see briefing.due). A message counts as sent only
    once someone is listening, so one that found nobody is offered again on the next check."""
    sent = 0
    for event in briefing.due(now or datetime.now()):
        if bus.publish(event) == 0:
            break
        briefing.mark_sent(event)
        sent += 1
    return sent


def sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"


_stop = threading.Event()


def _loop() -> None:
    while not _stop.wait(30):
        try:
            tick()
            nudge_tick()
        except Exception:
            log.exception("reminder check failed")


def start() -> None:
    _stop.clear()
    threading.Thread(target=_loop, name="reminders", daemon=True).start()


def stop() -> None:
    _stop.set()
