"""Checks an answer's citations without another LLM call.

For each "(file, page N)" citation: the cited location must be one that was actually retrieved,
and the claim just before the citation must be backed by it — the claim's numbers and proper
nouns have to appear in the cited text. Checking in this direction (claim -> source) works for
any chunk size, unlike scoring the whole chunk's facts against the answer.

Each citation gets a status: "checked" (its claim's facts are in the cited text), "unchecked"
(the claim has no number or name to check, so a pass would mean nothing), or "failed".
Sentences with checkable facts but no citation are returned as "uncited" spans."""
import re

LOC_KINDS = "page|part|slide|sheet|image|p\\."
CITATION_RE = re.compile(
    rf"[(\[]\s*([^\[\],]+?\.\w{{2,5}})\s*,\s*({LOC_KINDS})\s*(\d+)\s*[)\]]", re.IGNORECASE
)
_SENTENCE_END_RE = re.compile(r"[.!?]\s|\n")
# A full stop after these is an abbreviation ("roll no. 12", "Rs. 500", "Dr. Sharma"), not a sentence end.
_ABBREVIATIONS = {
    "no", "nos", "rs", "dr", "mr", "mrs", "ms", "st", "vs", "etc", "eg", "ie", "approx", "regn", "reg",
    "fig", "govt", "pvt", "ltd", "inc", "jr", "sr", "am", "pm", "viz", "vol", "ch", "sec", "dept", "acc",
    "a/c", "ref", "sl", "sr.no", "prof", "smt", "shri", "est", "max", "min", "qty", "amt",
}


def _sentence_ends(text: str, start: int = 0, end: int | None = None):
    """Sentence ends in text[start:end], skipping full stops that close an abbreviation or initial."""
    end = len(text) if end is None else end
    for m in _SENTENCE_END_RE.finditer(text, start, end):
        if text[m.start()] == ".":
            word = re.search(r"([A-Za-z][A-Za-z./]*)$", text[max(start, m.start() - 12):m.start()])
            w = word.group(1).lower().replace(".", "") if word else ""
            if w in _ABBREVIATIONS or (word and len(word.group(1).rstrip(".")) == 1):
                continue
        yield m
_NUMBER_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?")

STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "to", "of", "for", "and", "or",
    "this", "that", "it", "as", "by", "from", "with", "be", "has", "have", "source", "excerpt",
    "excerpts", "file", "page", "part", "slide", "sheet", "cited", "citation", "states", "according",
    "provided", "based", "solely", "answer", "question", "however", "also", "there", "they", "these",
    "your", "you", "yours", "my", "our", "their", "his", "her", "its", "what", "which", "who", "when",
    "where", "here", "yes", "sure", "thanks", "please",
}

THRESHOLD = 0.6
STRICT_THRESHOLD = 0.85  # Settings: "check citations strictly"
# ponytail: a negation word in the claim but none anywhere in the cited text fails the claim.
# Catches "the total is not $5,000"; misses negations the source happens to share. An NLI
# check would do better, at the cost of a second model pass.
# "no." is the abbreviation for number ("roll no. 12"), not a negation.
_NEGATION_RE = re.compile(r"\b(?:not|never|none|neither|nor)\b|\bno\b(?!\s*\.)|n't\b", re.IGNORECASE)


def _file_name(s: str) -> str:
    return re.split(r"[\\/]", s.strip())[-1].lower()


def _norm_num(s: str) -> str:
    return s.replace(",", "").replace("$", "").rstrip(".")


def salient_facts(text: str) -> set[str]:
    """Numbers and capitalised words: the parts of a claim that can be checked against a source."""
    numbers = {_norm_num(n) for n in _NUMBER_RE.findall(text)}
    nouns = {w.lower() for w in re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", text) if w.lower() not in STOPWORDS}
    return {n for n in numbers if n} | nouns


def _claim_before(answer: str, start: int, floor: int) -> str:
    """The sentence the citation closes: back to the previous sentence end or citation."""
    window = answer[floor:start]
    ends = [m.end() for m in _sentence_ends(window.rstrip())]
    return window[ends[-1]:] if ends else window


def _uncited(answer: str, claimed: list[tuple[int, int]]) -> list[list[int]]:
    """Spans of sentences outside every cited claim that still state something checkable."""
    spans, pos = [], 0
    for start, end in claimed + [(len(answer), len(answer))]:
        gap_start = pos
        for m in list(_sentence_ends(answer, pos, start)) + [None]:
            gap_end = m.end() if m else start
            sentence = answer[gap_start:gap_end]
            if sentence.strip() and salient_facts(sentence):
                lead = len(sentence) - len(sentence.lstrip())
                spans.append([gap_start + lead, gap_start + len(sentence.rstrip())])
            gap_start = gap_end
        pos = end
    return spans


def verify(answer_text: str, chunks: list[dict], threshold: float = THRESHOLD) -> dict:
    by_loc: dict[tuple[str, str, int], dict] = {}
    for c in chunks:
        key = (_file_name(c["path"]), c["loc_kind"].lower(), int(c["loc_no"]))
        entry = by_loc.setdefault(key, {"path": c["path"], "text": ""})
        entry["text"] += "\n" + c["text"]

    citations = []
    claimed: list[tuple[int, int]] = []
    numbering: dict[tuple, int] = {}
    floor = 0
    for m in CITATION_RE.finditer(answer_text):
        kind = m.group(2).lower()
        kind = "page" if kind == "p." else kind
        key = (_file_name(m.group(1)), kind, int(m.group(3)))
        n = numbering.setdefault(key, len(numbering) + 1)
        base = {"n": n, "file": m.group(1).strip(), "loc_kind": kind, "loc_no": key[2], "span": [m.start(), m.end()]}
        claim = _claim_before(answer_text, m.start(), floor)
        claimed.append((m.start() - len(claim), m.end()))
        floor = m.end()
        claim_text = CITATION_RE.sub("", claim).strip()
        check = _check(claim_text, by_loc.get(key), threshold)
        citations.append({**base, **check, "claim": claim_text, "verified": check["status"] == "checked"})
    return {
        "citations": citations,
        "uncited": _uncited(answer_text, claimed),
        "all_verified": bool(citations) and all(c["verified"] for c in citations),
    }


def _check(claim: str, source: dict | None, threshold: float = THRESHOLD) -> dict:
    if source is None:
        return {"status": "failed", "reason": "not in retrieved files"}
    out = {"path": source["path"]}
    facts = salient_facts(claim)
    if not facts:
        return {**out, "status": "unchecked", "overlap": None, "reason": "nothing specific to check"}
    # The cited file's own name counts as source text: "class 10" is backed by "class 10.pdf".
    text = source["text"] + "\n" + _file_name(source["path"])
    lower = text.lower()
    numbers = {_norm_num(x) for x in _NUMBER_RE.findall(text)}
    found = sum(1 for f in facts if (f in numbers if f[:1].isdigit() else f in lower))
    overlap = round(found / len(facts), 2)
    if overlap < threshold:
        return {**out, "status": "failed", "overlap": overlap, "reason": "claim not found in source"}
    if _NEGATION_RE.search(claim) and not _NEGATION_RE.search(source["text"]):
        return {**out, "status": "failed", "overlap": overlap, "reason": "claim negates the source"}
    return {**out, "status": "checked", "overlap": overlap, "reason": None}


EXCERPT_CHARS = 220


def excerpt(source_text: str, claim: str, chars: int = EXCERPT_CHARS) -> str:
    """The part of the cited text that backs the claim: centred on the claim's first number found
    there as a whole number (so "10" never matches inside "2101729"), else its first name; "" when
    neither is in the text, so the caller keeps its own snippet."""
    flat = " ".join(source_text.split())
    facts = salient_facts(claim)
    numbers = [f for f in facts if f[:1].isdigit()]
    names = [f for f in facts if not f[:1].isdigit()]
    at = -1
    for n in sorted(numbers, key=len, reverse=True):  # longest first: the roll number beats "10"
        digits = ",?".join(re.escape(ch) for ch in n)  # "1240" also matches "1,240"
        m = re.search(rf"(?<![\d.,]){digits}(?![\d])", flat)
        if m:
            at = m.start()
            break
    if at < 0:
        for w in names:
            m = re.search(rf"\b{re.escape(w)}\b", flat, re.IGNORECASE)
            if m:
                at = m.start()
                break
    if at < 0:
        return ""
    if len(flat) <= chars:
        return flat
    start = max(0, min(at - chars // 3, len(flat) - chars))
    if start:
        start = flat.find(" ", start) + 1 or start
    end = min(len(flat), start + chars)
    if end < len(flat):
        end = flat.rfind(" ", start, end) if flat.rfind(" ", start, end) > at else end
    return ("…" if start else "") + flat[start:end].strip() + ("…" if end < len(flat) else "")
