from pathlib import Path

import openpyxl

from app.core.chunking import Chunk, chunk_located

MAX_ROWS_PER_SHEET = 20_000  # bounds indexing time on huge exports


def parse_xlsx(path: Path) -> list[Chunk]:
    """Each sheet as "row N: a | b | c" lines, cited by 1-indexed sheet number. Uses cached
    cell values (data_only), so formulas contribute their last computed result."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    chunks: list[Chunk] = []
    try:
        for i, ws in enumerate(wb.worksheets, start=1):
            lines = [f"Sheet {ws.title}"]
            for r, row in enumerate(ws.iter_rows(values_only=True), start=1):
                if r > MAX_ROWS_PER_SHEET:
                    break
                cells = [str(v) for v in row if v is not None and str(v).strip()]
                if cells:
                    lines.append(f"row {r}: " + " | ".join(cells))
            if len(lines) > 1:
                chunks.extend(chunk_located("sheet", i, "\n".join(lines)))
    finally:
        wb.close()
    return chunks
