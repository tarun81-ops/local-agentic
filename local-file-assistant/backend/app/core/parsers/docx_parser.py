from pathlib import Path

import docx
from docx.table import Table

from app.core.chunking import Chunk, chunk_parts


def _table_text(table: Table) -> str:
    rows = []
    for row in table.rows:
        cells = [c.text.strip() for c in row.cells]
        rows.append(" | ".join(c for c in cells if c))
    return "\n".join(r for r in rows if r)


def parse_docx(path: Path) -> list[Chunk]:
    """Body paragraphs and tables in document order. Word files have no reliable page
    numbers, so chunks are cited as parts rather than pretending to be pages."""
    doc = docx.Document(path)
    blocks = []
    for item in doc.iter_inner_content():
        text = _table_text(item) if isinstance(item, Table) else item.text
        if text.strip():
            blocks.append(text)
    return chunk_parts("\n".join(blocks))
