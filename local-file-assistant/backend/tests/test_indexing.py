import docx
import openpyxl
from pptx import Presentation

from app.core import indexer
from app.core.chunking import split_words
from app.core.parsers import PARSERS
from app.db import sqlite_fts


def test_split_words_overlaps():
    words = [f"w{i}" for i in range(600)]
    pieces = split_words(" ".join(words), size=250, overlap=40)
    assert len(pieces) == 3
    assert pieces[1].split()[0] == "w210"  # starts 40 words before the end of piece 1
    assert pieces[-1].split()[-1] == "w599"


def test_docx_is_cited_by_part_not_page(corpus):
    chunks = PARSERS[".docx"](corpus / "meeting_notes.docx")
    assert chunks and {c.loc_kind for c in chunks} == {"part"}
    assert [c.loc_no for c in chunks] == list(range(1, len(chunks) + 1))


def test_pdf_pages(corpus):
    chunks = PARSERS[".pdf"](corpus / "invoice_notes.pdf")
    assert [(c.loc_kind, c.loc_no) for c in chunks] == [("page", 1), ("page", 2)]


def test_pptx_and_xlsx_parsers(tmp_path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "Launch plan"
    slide.placeholders[1].text = "Budget reserve 20000"
    prs.save(tmp_path / "deck.pptx")
    wb = openpyxl.Workbook()
    wb.active.title = "Q3"
    wb.active.append(["Item", "Amount"])
    wb.active.append(["Launch reserve", 20000])
    wb.save(tmp_path / "budget.xlsx")

    slides = PARSERS[".pptx"](tmp_path / "deck.pptx")
    assert slides[0].loc_kind == "slide" and "Budget reserve" in slides[0].text
    sheets = PARSERS[".xlsx"](tmp_path / "budget.xlsx")
    assert sheets[0].loc_kind == "sheet" and "row 2: Launch reserve | 20000" in sheets[0].text


def test_rescan_skips_unchanged_and_prunes_deleted(corpus, db_paths):
    db, vdb = db_paths
    first = indexer.index_folder(corpus, db, vdb)
    assert first["indexed"] == 2 and first["failed"] == 0
    second = indexer.index_folder(corpus, db, vdb)
    assert second["skipped"] == 2 and second["indexed"] == 0

    (corpus / "meeting_notes.docx").unlink()
    third = indexer.index_folder(corpus, db, vdb)
    assert third["removed"] == 1
    conn = sqlite_fts.connect(db)
    try:
        assert [p for p in sqlite_fts.indexed_paths(conn) if p.endswith("meeting_notes.docx")] == []
    finally:
        conn.close()


def test_broken_file_is_reported_with_a_reason(corpus, db_paths):
    (corpus / "broken.pdf").write_bytes(b"not a pdf")
    db, vdb = db_paths
    indexer.RECENT_ERRORS.clear()
    result = indexer.index_folder(corpus, db, vdb)
    assert result["failed"] == 1
    assert indexer.RECENT_ERRORS[0]["path"].endswith("broken.pdf") and indexer.RECENT_ERRORS[0]["error"]


def test_embedder_down_keeps_keyword_index_and_retries_later(corpus, db_paths, monkeypatch):
    def down(*_a, **_k):
        raise ConnectionError("ollama not running")

    monkeypatch.setattr(indexer, "embed", down)
    db, vdb = db_paths
    result = indexer.index_folder(corpus, db, vdb)
    assert result["indexed"] == 2 and result["semantic"] is False
    conn = sqlite_fts.connect(db)
    try:
        assert all(f["hash"] == "" for f in sqlite_fts.list_files(conn))  # retried on next scan
    finally:
        conn.close()


def test_office_lock_files_are_ignored(corpus):
    doc = docx.Document()
    doc.add_paragraph("lock")
    doc.save(corpus / "~$meeting_notes.docx")
    assert not indexer.is_indexable(corpus / "~$meeting_notes.docx")
