"""Failure modes from the research pass: Hindi words split by the tokenizers, Qwen3-Embedding query instruction,
sampling defaults, leaked <think> text, over-long prompts, unparseable JSON, and Hinglish questions that share
no word with the file."""

from app.config import settings


# ---------- Hindi words stay whole ----------
def test_words_keep_devanagari_vowel_signs():
    from app.core.search.fts_search import terms
    from app.core.words import strip_non_word, words

    assert words("गुनाहों का देवता, विद्यालय") == ["गुनाहों", "का", "देवता", "विद्यालय"]
    assert terms("गुनाहों का देवता") == ["गुनाहों", "का", "देवता"]
    assert strip_non_word("देवता!") == "देवता"


def test_content_index_matches_whole_hindi_words_only(tmp_path):
    from app.core.search.fts_search import build_match
    from app.db import sqlite_fts

    conn = sqlite_fts.connect(tmp_path / "index.db")
    try:
        conn.execute("INSERT INTO chunks(path, loc_kind, loc_no, chunk_no, text) VALUES ('a.pdf', 'page', 1, 0, 'गुनाहों का देवता उपन्यास')")
        conn.execute("INSERT INTO chunks(path, loc_kind, loc_no, chunk_no, text) VALUES ('b.pdf', 'page', 1, 0, 'दवा और दुकान')")

        def hits(q):
            return [r[0] for r in conn.execute("SELECT path FROM chunks WHERE chunks MATCH ?", (build_match(q),))]

        assert hits("देवता") == ["a.pdf"]
        assert hits("द") == []  # a lone letter fragment no longer matches every Hindi text
    finally:
        conn.close()


def test_memory_and_chat_recall_indexes_are_rebuilt_with_the_hindi_tokenizer(monkeypatch, tmp_path):
    from app.db import assistant_db

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    assistant_db.migrate()
    conn = assistant_db.connect()
    try:
        sql = dict(conn.execute("SELECT name, sql FROM sqlite_master WHERE name IN ('memories_fts', 'chat_index_fts')").fetchall())
    finally:
        conn.close()
    assert len(sql) == 2 and all("Mn Mc" in s for s in sql.values())


def test_keywords_for_passage_picking_keep_hindi_words():
    from app.core import pc_access

    assert pc_access.keywords("गुनाहों का देवता me Chander")[:3] == ["गुनाहों", "का", "देवता"]


# ---------- embeddings ----------
def test_passages_are_picked_by_meaning_when_no_word_matches(monkeypatch):
    from app.core.assistant import file_agent
    from app.core.chunking import Chunk
    from app.core.search import vector_search

    parsed = list(enumerate(Chunk(loc_kind="page", loc_no=i, text=f"chapter text number {i}") for i in range(1, 31)))
    # page 20 is the one that "means" the same as the question
    monkeypatch.setattr(vector_search, "embed", lambda texts, timeout=120: [[1.0, 0.0] if "number 20" in t else [0.0, 1.0] for t in texts])
    monkeypatch.setattr(vector_search, "embed_query", lambda texts, timeout=120: [[1.0, 0.0]])
    picked = file_agent.by_meaning("Sudha ki shaadi kisse hui", parsed)
    assert 20 in [c.loc_no for _, c in picked]


def test_meaning_pick_falls_back_quietly_without_an_embedder(monkeypatch):
    from app.core.assistant import file_agent
    from app.core.chunking import Chunk
    from app.core.search import vector_search

    def down(*a, **k):
        raise ConnectionError("OVMS not running")

    monkeypatch.setattr(vector_search, "embed", down)
    assert file_agent.by_meaning("kuch bhi", [(0, Chunk(loc_kind="page", loc_no=1, text="x"))]) is None
