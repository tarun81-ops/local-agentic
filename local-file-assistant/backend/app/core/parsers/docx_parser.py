from pathlib import Path

import docx


def parse_docx(path: Path, words_per_chunk: int = 500) -> list[tuple[int, str]]:
    """docx has no reliable page boundaries, so chunk_no stands in for page."""
    doc = docx.Document(path)
    words = " ".join(p.text for p in doc.paragraphs if p.text.strip()).split()
    chunks = []
    for i in range(0, len(words), words_per_chunk):
        chunk_words = words[i : i + words_per_chunk]
        if chunk_words:
            chunks.append((i // words_per_chunk + 1, " ".join(chunk_words)))
    return chunks
