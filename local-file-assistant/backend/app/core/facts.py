"""Exact answers for questions where one wrong character is a wrong answer (IFSC codes) and for "which file?" follow-ups.

The model is not trusted for these. IFSC codes are found with a fixed pattern in the text of the files that were read,
and the 4-letter bank prefix has to match the bank the person named (BARB = Bank of Baroda, SBIN = State Bank of India),
so a code from another bank's file is never presented as the answer. A follow-up that asks which file an answer came from
is answered from the citations stored with the previous answer; nothing is searched again."""
from __future__ import annotations

import re
from pathlib import Path

# IFSC: 4 letters, a 0, then 6 letters or digits. OCR often reads that 0 as O, so both are accepted and repaired.
_IFSC = re.compile(r"(?<![A-Z0-9])([A-Z]{4})[0O]([A-Z0-9]{6})(?![A-Z0-9])")

BANKS = {  # prefix -> (display name, words people use for it)
    "BARB": ("Bank of Baroda", ["bank of baroda", "baroda", "bob"]),
    "SBIN": ("State Bank of India", ["state bank of india", "sbi", "state bank"]),
    "HDFC": ("HDFC Bank", ["hdfc"]),
    "ICIC": ("ICICI Bank", ["icici"]),
    "UTIB": ("Axis Bank", ["axis"]),
    "PUNB": ("Punjab National Bank", ["punjab national", "pnb"]),
    "CNRB": ("Canara Bank", ["canara"]),
    "UBIN": ("Union Bank of India", ["union bank"]),
    "BKID": ("Bank of India", ["bank of india", "boi"]),
    "IDIB": ("Indian Bank", ["indian bank"]),
    "KKBK": ("Kotak Mahindra Bank", ["kotak"]),
    "YESB": ("Yes Bank", ["yes bank"]),
    "CBIN": ("Central Bank of India", ["central bank"]),
    "IOBA": ("Indian Overseas Bank", ["indian overseas", "iob"]),
    "MAHB": ("Bank of Maharashtra", ["bank of maharashtra"]),
    "IDFB": ("IDFC First Bank", ["idfc"]),
    "PYTM": ("Paytm Payments Bank", ["paytm"]),
}

ASKS_IFSC = re.compile(r"\bifs[cs]\b|\bifsc\s*code\b", re.I)
ASKS_SOURCE = re.compile(  # must point back at the previous answer ("this info", "you extracted"), not be a new request
    r"\bfrom which (?:file|document|source|pdf)\b"
    r"|\bwhere did you (?:get|find|take|read|see|extract)\b"
    r"|\b(?:which|what) (?:file|document|source|pdf)\b.{0,40}\b(?:this|that|it|answer|info|you)\b"
    r"|\b(?:mention|name|tell me|show me?|give me|open) the (?:file|source|document|pdf)\b.{0,50}\b(?:this|that|you|answer|info|extract\w*)\b"
    r"|\bsource of (?:this|that|the answer|the info)\b",
    re.I,
)


def asks_ifsc(message: str) -> bool:
    return bool(ASKS_IFSC.search(message))


def asks_source(message: str) -> bool:
    return bool(ASKS_SOURCE.search(message))


def bank_named(message: str) -> str | None:
    """Prefix of the bank the message names, longest alias first so 'bank of baroda' beats 'bank of india'."""
    low = " " + re.sub(r"[^a-z ]", " ", message.lower()) + " "
    best, best_len = None, 0
    for prefix, (_, aliases) in BANKS.items():
        for a in aliases:
            if f" {a} " in low and len(a) > best_len:
                best, best_len = prefix, len(a)
    return best


def find_ifsc(text: str) -> list[str]:
    out: list[str] = []
    for m in _IFSC.finditer(text.upper()):
        code = f"{m.group(1)}0{m.group(2)}"
        if code not in out:
            out.append(code)
    return out


def _bank_of(code: str) -> str:
    return BANKS.get(code[:4], (f"a bank with prefix {code[:4]}", []))[0]


def ifsc_reply(message: str, chunks: list[dict]) -> str | None:
    """A finished answer, or None when no IFSC code appears in what was read (the normal path then runs)."""
    wanted = bank_named(message)
    seen: dict[str, tuple[str, str, int]] = {}  # code -> (file name, loc kind, loc no) of the first place it appears
    for c in sorted(chunks, key=lambda c: (c["path"], int(c["loc_no"]), c.get("chunk_no", 0))):
        for code in find_ifsc(c["text"]):
            seen.setdefault(code, (Path(c["path"]).name, c["loc_kind"], int(c["loc_no"])))
    if not seen:
        return None
    matching = {k: v for k, v in seen.items() if not wanted or k.startswith(wanted)}
    if matching:
        who = f" of your {BANKS[wanted][0]} account" if wanted else ""
        lines = []
        for code, (name, kind, no) in list(matching.items())[:3]:
            tag = "p." if kind in ("page", "p.") else kind
            lines.append(f"{code} [{name}, {tag} {no}]")
        if len(lines) == 1:
            return f"The IFSC code{who} is {lines[0]}."
        return f"I found {len(lines)} IFSC codes{who}: " + "; ".join(lines) + ". Check which account you mean."
    others = ", ".join(f"{code} ({_bank_of(code)}) in {name}" for code, (name, _, _) in list(seen.items())[:3])
    return (f"I could not find a {BANKS[wanted][0]} IFSC code in the files I read. "
            f"The only IFSC code(s) I found: {others}. Those belong to a different bank, so I won't give them as your answer.")


def source_reply(history_messages: list[dict]) -> tuple[str, list[dict]] | None:
    """(answer text, citations) naming the files the previous answer was taken from, or None if it had none.
    The citations are the ones stored with that answer, so the same source cards (with OPEN) come back."""
    for m in reversed(history_messages):
        if m.get("role") != "assistant":
            continue
        cits = [c for c in (m.get("meta") or {}).get("citations", []) if c.get("path")]
        if not cits:
            return None
        uniq: list[dict] = []
        for c in cits:
            if not any(u["path"] == c["path"] and u["loc_no"] == c["loc_no"] for u in uniq):
                uniq.append(c)
        out: list[dict] = []
        shown = uniq[:3]
        text = "That answer was taken from " if len(shown) == 1 else "That answer was taken from these files: "
        for i, c in enumerate(shown, start=1):
            name = Path(c["path"]).name
            kind = "p." if c["loc_kind"] in ("page", "p.") else c["loc_kind"]
            marker = f"[{name}, {kind} {c['loc_no']}]"
            start = len(text)
            text += marker + (", " if i < len(shown) else "")
            out.append({**c, "n": i, "file": name, "span": [start, start + len(marker)]})
        text += f". It is on your computer at {shown[0]['path']}." if len(shown) == 1 else "."
        return text, out
    return None
