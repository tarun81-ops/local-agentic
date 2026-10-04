"""Keeps eval/assistant_eval.py honest: every case in its tables must pass, and run_eval's
answer-relevance heuristic must separate on-topic answers from off-topic ones."""
import importlib.util
from pathlib import Path

EVAL = Path(__file__).resolve().parents[1] / "eval"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, EVAL / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_assistant_eval_case_passes():
    results = _load("assistant_eval").run()
    assert [(area, failures) for area, _, failures in results if failures] == []
    assert sum(total for _, total, _ in results) >= 50


def test_relevance_flags_cited_answers_that_miss_the_question():
    run_eval = _load("run_eval")
    chunks = [{"path": "C:/d/invoice.pdf", "text": "Invoice total for Acme Corp: $4,250 due March"}, {"path": "C:/d/menu.pdf", "text": "Soup of the day"}]
    question = "What is the invoice total for Acme Corp?"
    on_topic = run_eval.relevance(question, "The total is $4,250 (invoice.pdf, page 1).", chunks, {"citations": [{"file": "invoice.pdf", "path": "C:/d/invoice.pdf"}]})
    off_topic = run_eval.relevance(question, "The soup of the day is tomato (menu.pdf, page 1).", chunks, {"citations": [{"file": "menu.pdf", "path": "C:/d/menu.pdf"}]})
    assert on_topic == 1.0 and off_topic < 0.5
    row = {"item": {}, "score": {"correct": True}, "first_token_s": 1.0, "total_s": 1.0}
    assert run_eval.summarize([{**row, "relevance": 0.5}, {**row, "relevance": 1.0}])["answer_relevance"] == 0.75
    assert run_eval.summarize([row])["answer_relevance"] is None  # older result files have no relevance
