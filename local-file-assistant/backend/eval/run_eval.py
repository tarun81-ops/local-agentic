"""Measures answer quality on the real local model, so model, prompt or chunking changes can
be compared with numbers instead of impressions.

    cd backend
    .venv\\Scripts\\python.exe eval\\run_eval.py                       # test_corpus + eval\\questions.jsonl
    .venv\\Scripts\\python.exe eval\\run_eval.py --folder "D:\\Docs" --questions my_questions.jsonl
    .venv\\Scripts\\python.exe eval\\run_eval.py --model qwen3-vl:4b-instruct

Each line of the questions file is JSON:
    {"q": "...", "expect": [["4,250", "4250"]], "file": "invoice_notes.pdf"}
  expect: every inner list must have at least one alternative in the answer (case-insensitive).
  file:   the file the answer should come from (retrieval + citation are checked against it).
  {"q": "...", "no_answer": true}  -> the files don't contain this; the model should say so.

Uses a throwaway index (never your real one) and needs Ollama running with the models pulled.
Writes eval\\results\\<time>.json and prints a summary."""
import argparse
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
REFUSAL_RE = re.compile(r"\b(not (found|mentioned|contain|include|provide|available)|no (information|mention)|doesn't|does not|don't|do not|cannot|can't|unable)\b", re.I)


def score(item: dict, answer: str, chunks: list[dict], verdict: dict) -> dict:
    """Scores one question. Pure function: covered by tests/test_eval.py."""
    cited_files = {Path(c.get("path") or c["file"]).name.lower() for c in verdict["citations"]}
    retrieved = [Path(c["path"]).name.lower() for c in chunks]
    if item.get("no_answer"):
        refused = bool(REFUSAL_RE.search(answer))
        verified_claims = any(c["verified"] for c in verdict["citations"])
        return {"correct": refused and not verified_claims, "refused": refused, "hallucinated_citation": verified_claims}
    want = item.get("file", "").lower()
    hits = [any(alt.lower() in answer.lower() for alt in alts) for alts in item.get("expect", [])]
    return {
        "correct": all(hits) if hits else None,
        "retrieved_expected_file": (want in retrieved) if want else None,
        "top1_expected_file": (retrieved[:1] == [want]) if want else None,
        "cited_expected_file": (want in cited_files) if want else None,
        "all_citations_verified": verdict["all_verified"],
    }


def summarize(rows: list[dict]) -> dict:
    def rate(key, subset=None):
        vals = [r["score"][key] for r in (subset or rows) if r["score"].get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    answerable = [r for r in rows if not r["item"].get("no_answer")]
    traps = [r for r in rows if r["item"].get("no_answer")]
    return {
        "questions": len(rows),
        "answer_correct": rate("correct", answerable),
        "retrieval_hit": rate("retrieved_expected_file", answerable),
        "retrieval_top1": rate("top1_expected_file", answerable),
        "citation_correct": rate("cited_expected_file", answerable),
        "citations_verified": rate("all_citations_verified", answerable),
        "no_answer_handled": rate("correct", traps),
        "median_first_token_s": _median([r["first_token_s"] for r in rows if r["first_token_s"] is not None]),
        "median_total_s": _median([r["total_s"] for r in rows]),
    }


def _median(xs):
    xs = sorted(xs)
    return round(xs[len(xs) // 2], 2) if xs else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folder", default=str(BACKEND.parent / "test_corpus"))
    ap.add_argument("--questions", default=str(HERE / "questions.jsonl"))
    ap.add_argument("--model", default=None, help="chat model (default: the one picked in Settings)")
    ap.add_argument("--top-k", type=int, default=6)
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="lfa-eval-"))
    os.environ.update({"DATA_DIR": str(tmp), "DB_PATH": str(tmp / "index.db"), "VECTOR_DB_DIR": str(tmp / "lancedb"), "API_TOKEN": "eval"})
    sys.path.insert(0, str(BACKEND))

    from app.config import settings
    from app.core import indexer, memory
    from app.core.llm.answerer import answer_stream
    from app.core.llm.client import current_model, ollama_status
    from app.core.llm.verifier import verify
    from app.core.search.hybrid import hybrid_search

    model = args.model or current_model()
    status = ollama_status()
    if not status["connected"]:
        print("Ollama isn't running. Start it and try again.")
        return 2
    if model not in status["models"]:
        print(f"{model} isn't pulled. Run: ollama pull {model}")
        return 2

    items = [json.loads(line) for line in Path(args.questions).read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"Indexing {args.folder} …")
    t0 = time.perf_counter()
    counts = indexer.index_folder(Path(args.folder), settings.db_path, settings.vector_db_dir)
    index_s = time.perf_counter() - t0
    print(f"  {counts['indexed']} files in {index_s:.1f}s (semantic search: {counts['semantic']})")

    rows = []
    min_free = None
    for n, item in enumerate(items, 1):
        chunks = hybrid_search(item["q"], limit=args.top_k)["results"]
        answer, first = "", None
        start = time.perf_counter()
        try:
            for delta in answer_stream(item["q"], chunks, model=model) if chunks else []:
                if first is None:
                    first = time.perf_counter() - start
                answer += delta
        except Exception as exc:
            answer = f"[error: {exc}]"
        total = time.perf_counter() - start
        free = memory.status()["free_mb"]
        if free is not None:
            min_free = free if min_free is None else min(min_free, free)
        verdict = verify(answer, chunks)
        s = score(item, answer, chunks, verdict)
        rows.append({"item": item, "answer": answer, "score": s, "first_token_s": first, "total_s": round(total, 2)})
        mark = {True: "PASS", False: "FAIL", None: "----"}[s["correct"]]
        print(f"[{n}/{len(items)}] {mark} {total:5.1f}s  {item['q']}")

    summary = {**summarize(rows), "model": model, "embedding_model": settings.embedding_model, "index_s": round(index_s, 1), "min_free_ram_mb": min_free}
    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{re.sub(r'[^A-Za-z0-9.-]+', '_', model)}.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    print("\n" + "\n".join(f"  {k:24} {v}" for k, v in summary.items()))
    print(f"\nSaved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
