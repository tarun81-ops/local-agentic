"""Marks arithmetic is done in code and cross-checked against the printed words."""
import json

from app.core import calc

SHEET_DIGIT_WORDS = (
    "CENTRAL BOARD OF SECONDARY EDUCATION MARKS STATEMENT CUM CERTIFICATE Roll No 12127198 "
    "SUB CODE SUBJECT MARKS OBTAINED THEORY PRACTICAL TOTAL TOTAL IN WORDS POSITIONAL GRADE "
    "002 HINDI COURSE-A 074 018 092 NINE TWO A1 "
    "184 ENGLISH LNG & LIT. 070 018 088 EIGHT EIGHT A2 "
    "041 MATHEMATICS STANDARD 075 015 090 NINE ZERO A1 "
    "086 SCIENCE 071 017 088 EIGHT EIGHT A2 "
    "087 SOCIAL SCIENCE 066 019 085 EIGHT FIVE A2 "
    "Result PASS"
)
SHEET_NUMBER_WORDS = "001 HINDI 085 EIGHTYFIVE A2 002 ENGLISH 090 NINETY A1 003 MATHEMATICS 080 EIGHTY A2 "


def test_words_to_int_both_styles_and_glued_ocr():
    assert calc.words_to_int("ZERO NINE TWO") == 92
    assert calc.words_to_int("NINETWO") == 92
    assert calc.words_to_int("NINETY TWO") == 92
    assert calc.words_to_int("EIGHTYFIVE") == 85
    assert calc.words_to_int("ONE ZERO ZERO") == 100
    assert calc.words_to_int("GRADE") is None


def test_percentage_is_exact_and_every_mark_checked_against_its_words():
    sheet = calc.parse_marksheet(SHEET_DIGIT_WORDS)
    assert [s.marks for s in sheet.subjects] == [92, 88, 90, 88, 85]
    assert all(s.checked for s in sheet.subjects)
    res = calc.compute(sheet)
    assert res.percent == 88.6 and "443 out of 500 = 88.6%" in res.working and res.caution == ""


def test_ordinary_number_words_work_too():
    sheet = calc.parse_marksheet(SHEET_NUMBER_WORDS)
    assert [s.marks for s in sheet.subjects] == [85, 90, 80] and all(s.checked for s in sheet.subjects)
    assert calc.compute(sheet).percent == 85


def test_an_ocr_misread_is_flagged_not_trusted():
    bad = SHEET_DIGIT_WORDS.replace("088 EIGHT EIGHT A2 041", "068 EIGHT EIGHT A2 041")  # 8 read as 6
    res = calc.compute(calc.parse_marksheet(bad))
    assert "English" in res.caution and "words say 88" in res.caution


def test_a_sixth_subject_uses_the_best_five():
    six = SHEET_DIGIT_WORDS.replace("Result PASS", "402 INFORMATION TECHNOLOGY 050 040 090 NINE ZERO A1 Result PASS")
    res = calc.compute(calc.parse_marksheet(six))
    assert "best five" in res.best_five and "out of 500" in res.working


def test_not_a_marksheet_returns_nothing():
    assert calc.answer_for("notes.txt", 1, "Meeting at 10 about the 2023 budget") is None


def test_answer_cites_the_file_and_page_and_says_the_app_did_the_sum():
    out = calc.answer_for("class 10.pdf", 1, SHEET_DIGIT_WORDS)
    assert "[class 10.pdf, p. 1]" in out and "Calculated by the app, not by the AI" in out and "= 88.6%" in out


def test_question_detector():
    assert calc.wanted("what is my class 10 percentage")
    assert calc.wanted("total marks in 10th")
    assert not calc.wanted("find my aadhaar card")


def test_chat_answers_a_percentage_question_without_the_model(monkeypatch):
    from app.api import routes_chat

    chunk = {"path": "C:/u/Downloads/class 10.pdf", "name": "class 10.pdf", "loc_kind": "page", "loc_no": 1,
             "chunk_no": 0, "text": SHEET_DIGIT_WORDS, "snippet": "x", "match": "pc"}
    other = dict(chunk, path="C:/u/Downloads/tilak class 12.pdf", name="tilak class 12.pdf")
    reply = routes_chat._marks_reply("what is my class 10 percentage", [other, chunk])
    assert "class 10.pdf" in reply and "tilak class 12" not in reply and "88.6%" in reply
    assert routes_chat._marks_reply("what is my percentage", [dict(chunk, text="nothing here")]) is None


def test_chat_route_streams_the_calculated_answer_with_a_verified_citation(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app.api import routes_chat
    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "db_path", tmp_path / "index.db")
    from app.core.assistant import chat_recall
    from app.db import assistant_db

    assistant_db.migrate()
    monkeypatch.setattr(chat_recall, "schedule_embed", lambda: None)
    chunk = {"path": "C:/u/Downloads/class 10.pdf", "name": "class 10.pdf", "loc_kind": "page", "loc_no": 1,
             "chunk_no": 0, "text": SHEET_DIGIT_WORDS, "snippet": "x", "match": "pc"}
    monkeypatch.setattr(routes_chat, "hybrid_search", lambda *a, **k: {"results": [chunk], "semantic": False})

    def boom(*a, **k):
        raise AssertionError("the model must not be used for the arithmetic")

    monkeypatch.setattr(routes_chat, "answer_stream", boom)
    monkeypatch.setattr(routes_chat, "chat_stream", boom)
    monkeypatch.setattr(routes_chat.pc_access, "enabled", lambda: False)
    monkeypatch.setattr(routes_chat, "_recall", lambda m: [])
    r = TestClient(app, base_url="http://127.0.0.1:8756").post("/chat", json={"message": "what is my class 10 percentage", "mode": "files"}, headers={"Authorization": "Bearer test-token"})
    body = r.text
    assert r.status_code == 200 and "88.6%" in body
    done = json.loads(body.split("event: done\ndata: ")[1].split("\n")[0])
    assert done["citations"] and done["citations"][0]["verified"] is True
