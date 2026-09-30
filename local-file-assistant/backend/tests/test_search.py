import pymupdf

from app.core import indexer
from app.core.search import fts_search, hybrid_ranker, vector_search
from app.db import sqlite_fts, vector_store


def test_build_match_drops_stopwords_and_ors_terms():
    assert fts_search.build_match("What is the invoice total for Acme Corp?") == '"invoice" OR "total" OR "acme" OR "corp"'


def test_build_match_neutralises_fts_syntax():
    # Quotes, NEAR and column filters in user input must not become FTS5 syntax.
    assert fts_search.build_match('acme" NEAR path:secret') == '"acme" OR "near" OR "path" OR "secret"'


def test_natural_question_finds_the_invoice(corpus, db_paths):
    db, vdb = db_paths
    indexer.index_folder(corpus, db, vdb)
    conn = sqlite_fts.connect(db)
    try:
        results = fts_search.search(conn, "What is the invoice total for Acme Corp?")
        assert results and results[0]["path"].endswith("invoice_notes.pdf")
        assert results[0]["loc_kind"] == "page" and results[0]["loc_no"] == 1
        assert fts_search.search(conn, '"""') == []  # nothing searchable, no error
    finally:
        conn.close()


def test_hybrid_merge_tags_match_kind():
    fts = [{"path": "a", "chunk_no": 0}, {"path": "b", "chunk_no": 0}]
    vec = [{"path": "b", "chunk_no": 0}, {"path": "c", "chunk_no": 0}]
    merged = hybrid_ranker.merge(fts, vec)
    assert merged[0]["path"] == "b" and merged[0]["match"] == "both"
    assert {r["match"] for r in merged[1:]} == {"keyword", "semantic"}


def test_semantic_search_and_root_filter(corpus, db_paths):
    db, vdb = db_paths
    indexer.index_folder(corpus, db, vdb)
    table = vector_store.open_table(vector_store.connect(vdb))
    hits = vector_search.search(table, "Everest budget approved", root=str(corpus.resolve()))
    assert hits and hits[0]["path"].endswith("meeting_notes.docx")
    assert vector_search.search(table, "Everest budget", root="C:/elsewhere") == []


def test_apostrophe_in_path_does_not_break_vector_upsert(tmp_path, db_paths):
    folder = tmp_path / "docs"
    folder.mkdir()
    target = folder / "O'Brien notes.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Contract with O'Brien signed for 900 dollars.")
    doc.save(target)
    db, vdb = db_paths
    indexer.index_folder(folder, db, vdb)
    target.write_bytes(target.read_bytes() + b"\n")  # new hash forces a re-index of the same path
    indexer.index_folder(folder, db, vdb)
    rows = vector_store.open_table(vector_store.connect(vdb)).to_arrow().to_pylist()
    assert len([r for r in rows if r["path"] == str(target.resolve())]) == 1
