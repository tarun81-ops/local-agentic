import re
from pathlib import Path

CITATION_RE = re.compile(r"[(\[]\s*([^,()\[\]]+?),\s*page\s*(\d+)", re.IGNORECASE)

STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "to", "of", "for",
    "and", "or", "this", "that", "it", "as", "by", "from", "with", "be", "has", "have",
    "source", "excerpt", "excerpts", "file", "page", "cited", "citation", "states",
    "according", "provided", "based", "solely", "answer", "question",
}


def _context_window(text: str, pos: int, radius: int = 150) -> str:
    """A fixed character window rather than sentence-splitting, since models often put the
    citation bracket on its own line separate from the claim sentence."""
    return text[max(0, pos - radius) : pos + radius]


def _salient_facts(text: str) -> set[str]:
    """Numbers and proper nouns as a proxy for a chunk's verifiable facts — deliberately not
    generic word-overlap, since a verbose answer's filler prose ("Based on the provided
    excerpts, I can answer...") dilutes a symmetric overlap ratio even when correctly grounded."""
    numbers = re.findall(r"\$?\d[\d,]*\.?\d*", text)
    proper_nouns = re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", text)
    return {n for n in numbers} | {p.lower() for p in proper_nouns if p.lower() not in STOPWORDS}


def verify(answer_text: str, chunks: list[dict], overlap_threshold: float = 0.5) -> dict:
    """No LLM call: confirms each (file, page) citation matches a chunk that was actually
    retrieved, and that the cited chunk's own salient facts (numbers, proper nouns) actually
    show up near the citation — catches misattribution (citing a real page that doesn't back
    up the specific claim) without an LLM call."""
    by_key = {(Path(c["path"]).name.lower(), c["chunk_no"]): c for c in chunks}
    citations = []
    for m in CITATION_RE.finditer(answer_text):
        cited_file, cited_page = m.group(1).strip(), int(m.group(2))
        chunk = by_key.get((Path(cited_file).name.lower(), cited_page))
        if chunk is None:
            citations.append(
                {"file": cited_file, "page": cited_page, "verified": False, "reason": "not in retrieved chunks"}
            )
            continue
        window = _context_window(answer_text, m.start()).lower()
        chunk_facts = _salient_facts(chunk["text"])
        if not chunk_facts:
            citations.append({"file": cited_file, "page": cited_page, "verified": True, "overlap": None})
            continue
        found = sum(1 for fact in chunk_facts if fact.lower() in window)
        overlap = found / len(chunk_facts)
        citations.append(
            {
                "file": cited_file,
                "page": cited_page,
                "verified": overlap >= overlap_threshold,
                "overlap": round(overlap, 2),
            }
        )
    return {"citations": citations, "all_verified": bool(citations) and all(c["verified"] for c in citations)}


def _demo():
    chunks = [
        {"path": "invoice_notes.pdf", "chunk_no": 1, "text": "The invoice total for Acme Corp is $4,250 due March 3rd."},
        {"path": "invoice_notes.pdf", "chunk_no": 2, "text": "Payment terms: net 30 days from the invoice date."},
    ]

    correct = verify("The invoice total for Acme Corp is $4,250 (invoice_notes.pdf, page 1).", chunks)
    assert correct["all_verified"], correct

    hallucinated = verify("The invoice total is $4,250 (fake_receipt.pdf, page 9).", chunks)
    assert not hallucinated["all_verified"]
    assert hallucinated["citations"][0]["reason"] == "not in retrieved chunks"

    misattributed = verify("Payment is due within 14 days of delivery (invoice_notes.pdf, page 1).", chunks)
    assert not misattributed["all_verified"], misattributed

    print("verifier self-check OK")


if __name__ == "__main__":
    _demo()
