"""IFSC codes are checked against the bank named; 'which file?' reuses the previous answer's source."""
import json

from app.core import facts
from app.core.llm.verifier import verify


def chunk(path, text, no=1):
    return {"path": path, "name": path.split("/")[-1], "loc_kind": "page", "loc_no": no, "chunk_no": 0, "text": text, "snippet": text[:50], "match": "pc"}


SBI = chunk("C:/u/Downloads/passbook k (1).pdf", "State Bank of India Account No 123 IFSC Code : SBIN0004572 Branch Raipur")
BOB = chunk("C:/u/Documents/bob passbook.pdf", "Bank of Baroda Savings IFSC BARB0RAIPUR Branch Raipur")


def test_finds_ifsc_and_repairs_an_ocr_o_for_zero():
    assert facts.find_ifsc("IFSC: SBIN0004572 and some other text 12345") == ["SBIN0004572"]
    assert facts.find_ifsc("IFSC BARBOKHAMHA") == ["BARB0KHAMHA"]  # 0 read as O


def test_bank_named():
    assert facts.bank_named("what is IFSC code of my bank of baroda passbook") == "BARB"
    assert facts.bank_named("ifsc of my sbi account") == "SBIN"
    assert facts.bank_named("what is my ifsc") is None


def test_another_banks_code_is_never_given_as_the_answer():
    out = facts.ifsc_reply("what is IFSC code of my bank of baroda passbook", [SBI])
    assert "SBIN0004572" in out and "could not find a Bank of Baroda IFSC" in out and "won't give them as your answer" in out
    assert "The IFSC code of your" not in out


def test_matching_bank_gives_a_cited_answer():
    out = facts.ifsc_reply("what is IFSC code of my bank of baroda passbook", [SBI, BOB])
    assert out == "The IFSC code of your Bank of Baroda account is BARB0RAIPUR [bob passbook.pdf, p. 1]."
    done = verify(out, [SBI, BOB])
    assert done["citations"][0]["verified"] is True


def test_no_ifsc_anywhere_falls_through_to_the_normal_path():
    assert facts.ifsc_reply("ifsc code", [chunk("C:/u/a.pdf", "nothing here")]) is None


def test_citation_with_brackets_in_the_file_name_is_recognised():
    ans = "The code is SBIN0004572 [passbook k (1).pdf, page 1]."
    done = verify(ans, [SBI])
    assert done["citations"] and done["citations"][0]["verified"] is True and not done["uncited"]


def test_source_question_detector_needs_to_point_back_at_the_answer():
    assert facts.asks_source("can you mention the file from which you have extracted this info")
    assert facts.asks_source("which file is this from")
    assert facts.asks_source("where did you get that")
    assert not facts.asks_source("show me the statement")
    assert not facts.asks_source("find my bank of baroda passbook")


def test_source_reply_names_the_one_original_file_and_its_location():
    msgs = [
        {"role": "user", "content": "q", "meta": {}},
        {"role": "assistant", "content": "a", "meta": {"citations": [
            {"path": "C:/u/Downloads/passbook k (1).pdf", "loc_kind": "page", "loc_no": 1, "status": "checked", "verified": True, "snippet": "s"},
            {"path": "C:/u/Downloads/passbook k (1).pdf", "loc_kind": "page", "loc_no": 1, "status": "checked", "verified": True},
        ]}},
    ]
    text, cits = facts.source_reply(msgs)
    assert len(cits) == 1 and cits[0]["path"].endswith("passbook k (1).pdf") and cits[0]["snippet"] == "s"
    assert "C:/u/Downloads/passbook k (1).pdf" in text and text[cits[0]["span"][0]:cits[0]["span"][1]] == "[passbook k (1).pdf, p. 1]"
    assert facts.source_reply([{"role": "assistant", "content": "a", "meta": {}}]) is None


def test_chat_route_follow_up_does_not_search_again(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app.api import routes_chat
    from app.config import settings
    from app.core.assistant import chat_recall, conversation
    from app.db import assistant_db
    from app.main import app

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "db_path", tmp_path / "index.db")
    assistant_db.migrate()
    monkeypatch.setattr(chat_recall, "schedule_embed", lambda: None)
    conv = conversation.create("t")
    conversation.add_messages(conv["id"], [("user", "ifsc?", {}), ("assistant", "x", {"citations": [
        {"path": "C:/u/Downloads/passbook k (1).pdf", "loc_kind": "page", "loc_no": 1, "status": "checked", "verified": True}]})])

    def boom(*a, **k):
        raise AssertionError("a source question must not search again")

    monkeypatch.setattr(routes_chat, "hybrid_search", boom)
    r = TestClient(app, base_url="http://127.0.0.1:8756").post(
        "/chat", json={"message": "can you mention the file from which you have extracted this info", "conversation_id": conv["id"]},
        headers={"Authorization": "Bearer test-token"})
    done = json.loads(r.text.split("event: done\ndata: ")[1].split("\n")[0])
    assert r.status_code == 200 and "passbook k (1).pdf" in r.text and len(done["citations"]) == 1
