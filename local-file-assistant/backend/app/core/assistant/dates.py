"""Natural-language dates for reminders. Our own regex finds the time phrase in a sentence and
dateparser turns only that phrase into a date (dateparser's own sentence search is unreliable:
it reads "me" as May). Dates without a time get 09:00 and come back as confidence "date_only",
so the confirm card can flag them instead of guessing."""
import re
from datetime import datetime

import dateparser

_MONTHS = r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
_WEEKDAY = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tues?|wed|thu(?:rs?)?|fri|sat|sun"
_DAY = rf"(?:today|tomorrow|day after tomorrow|(?:(?:next|this|coming)\s+)?(?:{_WEEKDAY})|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})|(?:{_MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?)"
_TIME = r"(?:\d{1,2}(?::\d{2})?\s?(?:am|pm)|\d{1,2}:\d{2}|noon|midnight|morning|afternoon|evening|tonight)"
_REL = r"in\s+\d+\s+(?:minutes?|mins?|hours?|hrs?|days?|weeks?)"
_PHRASE = re.compile(rf"\b(?:(?:at|by|on|before)\s+)?(?:{_REL}|{_DAY}(?:,?\s+(?:at\s+|in\s+the\s+)?{_TIME})?|{_TIME}(?:\s+(?:on\s+)?{_DAY})?)\b", re.I)
_HAS_TIME = re.compile(rf"{_TIME}|{_REL}", re.I)
_PARTS = {"morning": "9am", "afternoon": "3pm", "evening": "6pm", "noon": "12pm", "midnight": "12am"}


def _normalise(phrase: str) -> str:
    p = re.sub(r"^(?:at|by|on|before)\s+", "", phrase.lower())
    p = re.sub(rf"\b(?:next|this|coming)\s+(?=(?:{_WEEKDAY})\b)", "", p)  # a bare weekday already means the next one
    p = p.replace("in the ", "").replace("tonight", "today 8pm")
    for word, clock in _PARTS.items():
        p = re.sub(rf"\b{word}\b", clock, p)
    return re.sub(r"[,\s]+", " ", p).strip()


def extract(text: str, now: datetime | None = None) -> dict:
    """{when: naive local datetime | None, confidence: "high" | "date_only" | "none", phrase, rest}.
    `rest` is the text with the time phrase removed."""
    now = now or datetime.now()
    none = {"when": None, "confidence": "none", "phrase": None, "rest": text.strip()}
    best = max(_PHRASE.finditer(text), key=lambda m: len(m.group(0)), default=None)
    if best is None:
        return none
    when = dateparser.parse(_normalise(best.group(0)), settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": now})
    if when is None:
        return none
    timed = bool(_HAS_TIME.search(best.group(0)))
    if not timed:
        when = when.replace(hour=9, minute=0, second=0, microsecond=0)
    rest = f"{text[: best.start()]} {text[best.end() :]}"
    return {"when": when, "confidence": "high" if timed else "date_only", "phrase": best.group(0), "rest": " ".join(rest.split())}
