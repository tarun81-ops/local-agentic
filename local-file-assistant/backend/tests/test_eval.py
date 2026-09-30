"""The eval harness's scoring (the model run itself needs Ollama: eval/run_eval.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
from run_eval import score, summarize  # noqa: E402

CHUNKS = [{"path": "C:/docs/invoice_notes.pdf"}, {"path": "C:/docs/meeting_notes.docx"}]


def test_correct_cited_answer():
    item = {"q": "total?", "expect": [["4,250", "4250"]], "file": "invoice_notes.pdf"}
    verdict = {"citations": [{"path": "C:/docs/invoice_notes.pdf", "verified": True}], "all_verified": True}
    s = score(item, "The total is $4,250 (invoice_notes.pdf, page 1).", CHUNKS, verdict)
    assert s == {"correct": True, "retrieved_expected_file": True, "top1_expected_file": True, "cited_expected_file": True, "all_citations_verified": True}


def test_wrong_number_fails():
    item = {"q": "total?", "expect": [["4,250"]], "file": "invoice_notes.pdf"}
    s = score(item, "The total is $5,000.", CHUNKS, {"citations": [], "all_verified": False})
    assert s["correct"] is False and s["cited_expected_file"] is False


def test_no_answer_question():
    item = {"q": "salary?", "no_answer": True}
    good = score(item, "The excerpts don't mention a salary.", CHUNKS, {"citations": [], "all_verified": False})
    bad = score(item, "It is $1M (invoice_notes.pdf, page 1).", CHUNKS, {"citations": [{"file": "invoice_notes.pdf", "verified": True}], "all_verified": True})
    assert good["correct"] and not bad["correct"] and bad["hallucinated_citation"]


def test_summary():
    rows = [
        {"item": {}, "score": {"correct": True, "retrieved_expected_file": True, "top1_expected_file": False, "cited_expected_file": True, "all_citations_verified": True}, "first_token_s": 1.0, "total_s": 4.0},
        {"item": {"no_answer": True}, "score": {"correct": False}, "first_token_s": None, "total_s": 2.0},
    ]
    s = summarize(rows)
    assert s["answer_correct"] == 1.0 and s["retrieval_top1"] == 0.0 and s["no_answer_handled"] == 0.0
    assert s["median_total_s"] == 4.0
