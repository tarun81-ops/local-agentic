"""Runnable check for the vertical slice: index -> hybrid search -> (optional) LLM answer.
Run: .venv\\Scripts\\python.exe test_vertical_slice.py
"""
import os
import time
from pathlib import Path

import psutil

from app.core.indexer import index_folder
from app.core.search import fts_search, hybrid_ranker, vector_search
from app.db.sqlite_fts import connect
from app.db.vector_store import connect as vconnect

CORPUS = Path(__file__).resolve().parents[1] / "test_corpus"
DB_PATH = Path(__file__).resolve().parent / "data" / "smoke_test.db"
VECTOR_DB_DIR = Path(__file__).resolve().parent / "data" / "smoke_test_lancedb"

proc = psutil.Process()


def rss_mb() -> float:
    return proc.memory_info().rss / 1024 / 1024


def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.unlink(missing_ok=True)
    if VECTOR_DB_DIR.exists():
        import shutil

        shutil.rmtree(VECTOR_DB_DIR)

    t0 = time.perf_counter()
    stats = index_folder(CORPUS, DB_PATH, VECTOR_DB_DIR)
    index_s = time.perf_counter() - t0
    print(f"indexed={stats['indexed']} skipped={stats['skipped']} in {index_s:.3f}s, rss={rss_mb():.1f}MB")
    assert stats["indexed"] == 2, "expected 2 files (1 pdf + 1 docx) in test_corpus"

    conn = connect(DB_PATH)
    vdb = vconnect(VECTOR_DB_DIR)
    table = vdb.open_table("chunks")

    t0 = time.perf_counter()
    fts_results = fts_search.search(conn, "Acme Corp invoice")
    fts_s = time.perf_counter() - t0
    print(f"fts search: {fts_s * 1000:.1f}ms -> {fts_results[0]['path']} chunk {fts_results[0]['chunk_no']}")
    assert fts_results and fts_results[0]["path"].endswith("invoice_notes.pdf")

    # Paraphrased query with no literal keyword overlap — only semantic search should find this.
    t0 = time.perf_counter()
    vec_results = vector_search.search(table, "how much money is owed and by when")
    vec_s = time.perf_counter() - t0
    print(f"vector search: {vec_s * 1000:.1f}ms -> {vec_results[0]['path']} chunk {vec_results[0]['chunk_no']}")
    assert vec_results and vec_results[0]["path"].endswith("invoice_notes.pdf")

    merged = hybrid_ranker.merge(fts_results, vec_results)
    print(f"hybrid merge -> {merged[0]['path']} chunk {merged[0]['chunk_no']}")
    assert merged and merged[0]["path"].endswith("invoice_notes.pdf")

    everest_results = fts_search.search(conn, "Everest project budget")
    print(f"fts search 2 -> {everest_results[0]['path']} chunk {everest_results[0]['chunk_no']}")
    assert everest_results and everest_results[0]["path"].endswith("meeting_notes.docx")

    conn.close()

    models = [m for m in os.environ.get("OLLAMA_MODELS", "").split(",") if m] or (
        [os.environ["OLLAMA_MODEL"]] if os.environ.get("OLLAMA_MODEL") else []
    )
    if not models:
        print("OLLAMA_MODEL(S) not set — skipping LLM answer step. Set it and rerun to test the full slice.")
        return

    from app.core.llm.answerer import answer_stream
    from app.core.llm.verifier import verify

    for model in models:
        t0 = time.perf_counter()
        first_token_s = None
        text = ""
        for delta in answer_stream("What is the invoice total for Acme Corp?", merged, model=model):
            if first_token_s is None:
                first_token_s = time.perf_counter() - t0
            text += delta
        total_s = time.perf_counter() - t0
        print(
            f"\n[{model}] first token in {first_token_s:.2f}s, full answer in {total_s:.2f}s, "
            f"rss={rss_mb():.1f}MB:\n{text}"
        )
        result = verify(text, merged)
        print(f"verify: all_verified={result['all_verified']} citations={result['citations']}")


if __name__ == "__main__":
    main()
    print("OK")
