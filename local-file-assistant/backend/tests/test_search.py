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


def test_reranker_reorders_hybrid_results_and_failures_fall_back(corpus, db_paths, monkeypatch):
    from app.core.search import hybrid, reranker

    db, vdb = db_paths
    indexer.index_folder(corpus, db, vdb)
    monkeypatch.setattr(hybrid.settings, "db_path", db)
    monkeypatch.setattr(hybrid.settings, "vector_db_dir", vdb)
    q = "What is the invoice total for Acme Corp?"

    plain = hybrid.hybrid_search(q, limit=3)
    assert not plain["reranked"] and plain["results"][0]["name"] == "invoice_notes.pdf"

    # A stand-in cross-encoder that prefers the meeting notes: re-ranking must follow it.
    monkeypatch.setattr(reranker, "available", lambda: True)
    monkeypatch.setattr(reranker, "scores", lambda query, passages, name=None: [float("meeting_notes" in p) for p in passages])
    out = hybrid.hybrid_search(q, limit=3)
    assert out["reranked"] and out["results"][0]["name"] == "meeting_notes.docx"

    def broken(*a, **k):
        raise RuntimeError("corrupt model file")

    monkeypatch.setattr(reranker, "scores", broken)
    out = hybrid.hybrid_search(q, limit=3)
    assert not out["reranked"] and out["results"][0]["name"] == "invoice_notes.pdf"


def test_reranker_is_off_without_a_model_and_reads_the_file_name(tmp_path, monkeypatch):
    from app.core.search import reranker

    monkeypatch.setattr(reranker.settings, "models_dir", tmp_path)
    monkeypatch.setattr(reranker.settings, "rerank_model", "")
    assert not reranker.available()
    monkeypatch.setattr(reranker.settings, "rerank_model", "org/model")
    assert not reranker.available()  # configured but never downloaded
    for f in reranker.REMOTE_FILES:
        (reranker.model_dir("org/model") / f).parent.mkdir(parents=True, exist_ok=True)
        (reranker.model_dir("org/model") / f).write_text("x")
    assert reranker.available()
    assert reranker.passage({"path": "C:/docs/Insurance/car_policy.pdf", "text": "Motor policy"}).startswith(
        "File: car_policy.pdf\nFolder: Insurance\n"
    )
