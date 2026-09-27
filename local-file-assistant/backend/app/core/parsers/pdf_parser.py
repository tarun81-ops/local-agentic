from pathlib import Path

import pymupdf as fitz


def parse_pdf(path: Path) -> list[tuple[int, str]]:
    """Returns [(page_number, text), ...], 1-indexed, skipping blank pages."""
    doc = fitz.open(path)
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text().strip()
        if text:
            pages.append((i + 1, text))
    doc.close()
    return pages
