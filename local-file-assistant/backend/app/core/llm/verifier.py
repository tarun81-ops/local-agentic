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
    rf"[(\[]\s*([^()\[\],]+?\.\w{{2,5}})\s*,\s*({LOC_KINDS})\s*(\d+)\s*[)\]]", re.IGNORECASE
)
_SENTENCE_END = re.compile(r"[.!?]\s|\n")
_NUMBER_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?")

STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "to", "of", "for", "and", "or",
    "this", "that", "it", "as", "by", "from", "with", "be", "has", "have", "source", "excerpt",
    "excerpts", "file", "page", "part", "slide", "sheet", "cited", "citation", "states", "according",
    "provided", "based", "solely", "answer", "question", "however", "also", "there", "they", "these",
}

THRESHOLD = 0.6
# ponytail: a negation word in the claim but none anywhere in the cited text fails the claim.
# Catches "the total is not $5,000"; misses negations the source happens to share. An NLI
# check would do better, at the cost of a second model pass.
_NEGATION_RE = re.compile(r"\b(?:not|no|never|none|neither|nor)\b|n't\b", re.IGNORECASE)


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
    ends = [m.end() for m in _SENTENCE_END.finditer(window.rstrip())]
    return window[ends[-1]:] if ends else window


def _uncited(answer: str, claimed: list[tuple[int, int]]) -> list[list[int]]:
    """Spans of sentences outside every cited claim that still state something checkable."""
    spans, pos = [], 0
    for start, end in claimed + [(len(answer), len(answer))]:
        gap_start = pos
        for m in list(_SENTENCE_END.finditer(answer, pos, start)) + [None]:
            gap_end = m.end() if m else start
            sentence = answer[gap_start:gap_end]
            if sentence.strip() and salient_facts(sentence):
                lead = len(sentence) - len(sentence.lstrip())
                spans.append([gap_start + lead, gap_start + len(sentence.rstrip())])
            gap_start = gap_end
        pos = end
    return spans


def verify(answer_text: str, chunks: list[dict]) -> dict:
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
        check = _check(CITATION_RE.sub("", claim), by_loc.get(key))
        citations.append({**base, **check, "verified": check["status"] == "checked"})
    return {
        "citations": citations,
        "uncited": _uncited(answer_text, claimed),
        "all_verified": bool(citations) and all(c["verified"] for c in citations),
    }


def _check(claim: str, source: dict | None) -> dict:
    if source is None:
        return {"status": "failed", "reason": "not in retrieved files"}
    out = {"path": source["path"]}
    facts = salient_facts(claim)
    if not facts:
        return {**out, "status": "unchecked", "overlap": None, "reason": "nothing specific to check"}
    lower = source["text"].lower()
    numbers = {_norm_num(x) for x in _NUMBER_RE.findall(source["text"])}
    found = sum(1 for f in facts if (f in numbers if f[:1].isdigit() else f in lower))
    overlap = round(found / len(facts), 2)
    if overlap < THRESHOLD:
        return {**out, "status": "failed", "overlap": overlap, "reason": "claim not found in source"}
    if _NEGATION_RE.search(claim) and not _NEGATION_RE.search(source["text"]):
        return {**out, "status": "failed", "overlap": overlap, "reason": "claim negates the source"}
    return {**out, "status": "checked", "overlap": overlap, "reason": None}
