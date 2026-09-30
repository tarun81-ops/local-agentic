from dataclasses import dataclass

# ~250 words keeps each chunk well inside what a small embedding model represents well;
# the 40-word overlap stops a fact that straddles a boundary from being lost to both sides.
WORDS_PER_CHUNK = 250
OVERLAP_WORDS = 40


@dataclass(frozen=True)
class Chunk:
    """loc_kind is how a citation names the location: "page" (PDF), "slide" (pptx),
    "sheet" (xlsx), "part" (docx, which has no reliable page numbers) or "image"."""

    loc_kind: str
    loc_no: int
    text: str


def split_words(text: str, size: int = WORDS_PER_CHUNK, overlap: int = OVERLAP_WORDS) -> list[str]:
    words = text.split()
    if not words:
        return []
    if len(words) <= size:
        return [" ".join(words)]
    step = size - overlap
    pieces = []
    for start in range(0, len(words), step):
        pieces.append(" ".join(words[start : start + size]))
        if start + size >= len(words):
            break
    return pieces


def chunk_located(loc_kind: str, loc_no: int, text: str) -> list[Chunk]:
    """Chunks one page/slide/sheet; every piece keeps that location for its citation."""
    return [Chunk(loc_kind, loc_no, piece) for piece in split_words(text)]


def chunk_parts(text: str) -> list[Chunk]:
    """For formats without pages: the pieces are numbered as parts 1, 2, 3…"""
    return [Chunk("part", i, piece) for i, piece in enumerate(split_words(text), start=1)]
