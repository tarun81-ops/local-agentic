"""'What is my class 12 roll no' must come from the user's own marksheet: not a friend's 12th.pdf, and also when
the user's copy is a phone photo called IMG-20250626-WA0122.jpg (the user's real case, Oct 2026)."""
import json
import re

import pytest

from app.api import routes_chat
from app.config import settings
from app.core import doc_photos, owner, pc_access, personalize
from app.core.assistant import file_agent
from app.core.chunking import chunk_located

FRIEND_12 = ("CENTRAL BOARD OF SECONDARY EDUCATION MARKS STATEMENT CUM CERTIFICATE SENIOR SCHOOL CERTIFICATE EXAMINATION 2025\n"
             "This is to certify that\nName of Candidate RAHUL SAHU\nMother's Name SUNITA SAHU\nRoll No 12626405")
FRIEND_PET = ("Pre Engineering Test (PET26)-2026 Basic Details Roll No. : 25071238 Application Number : PET260007096429 "
              "Candidate Name : NITISH KHUNTE Relative's Name : SHANKAR KHUNTE Gender : Male Class 12 marks")
MINE_PHOTO = ("CENTRAL BOARD OF SECONDARY EDUCATION\nSENIOR SCHOOL CERTIFICATE EXAMINATION 2025\nMARKS STATEMENT\n"
              "Name of Candidate TARUN VERMA\nFather's Name RAM VERMA\nRoll No 26139847")


# ---------- owner.py ----------

@pytest.mark.parametrize("msg,name", [
    ("you fetch another persons marksheet my name is tarun verma", "Tarun Verma"),
    ("my name is Tarun Verma and i want my roll no", "Tarun Verma"),
    ("mera naam tarun verma hai", "Tarun Verma"),
    ("what is my name", ""),
    ("i am in class 12", ""),
])
def test_the_name_the_user_types_is_picked_up(msg, name):
    assert owner.stated(msg) == name


def test_name_fields_skip_parents_and_find_the_candidate():
    assert owner.named_people(FRIEND_PET) == ["Nitish Khunte"]
    assert owner.named_people(MINE_PHOTO) == ["Tarun Verma"]
    assert owner.check(FRIEND_PET, "Tarun Verma") == ("no", ["Nitish Khunte"])
    assert owner.check(MINE_PHOTO, "tarun verma")[0] == "yes"
    assert owner.check("TARUN VERMAA roll 1", "Tarun Verma")[0] == "yes"  # an OCR slip
    assert owner.check("roll no 5 total 400", "Tarun Verma")[0] == "unknown"


def test_only_personal_document_questions_check_the_owner():
    assert owner.about_me("what is my class 12 roll no.")
    assert owner.about_me("meri marksheet ka percentage")
    assert not owner.about_me("summarize my notes")
    assert not owner.about_me("what is the roll no in mark.pdf")


# ---------- doc_photos.py ----------

def test_a_photo_card_keeps_the_kind_and_the_start_of_the_text():
    is_doc, head = doc_photos.card_text(MINE_PHOTO)
    assert is_doc and head.startswith("marksheet certificate class 12") and "TARUN VERMA" in head
    assert len(head) <= doc_photos.HEAD_CHARS + 80
    assert doc_photos.card_text("hello") == (False, "")


@pytest.fixture
def pc(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    drive = tmp_path / "drive"
    docs = drive / "Users" / "me" / "Downloads"
    phone = drive / "Users" / "me" / "CrossDevice" / "WhatsApp Documents"
    docs.mkdir(parents=True)
    phone.mkdir(parents=True)
    (docs / "12th.txt").write_text(FRIEND_12)
    (docs / "mark class 12.txt").write_text(FRIEND_PET)
    (phone / "IMG-20250626-WA0122.jpg").write_bytes(b"\xff\xd8" + b"0" * 30_000)
    (phone / "IMG-20250626-WA0123.jpg").write_bytes(b"\xff\xd8" + b"1" * 30_000)  # a holiday photo
    pc_access.set_config(enabled=True, drives=[str(drive)])
    pc_access.scan([str(drive)])

    ocr = {"IMG-20250626-WA0122.jpg": MINE_PHOTO, "IMG-20250626-WA0123.jpg": "beach"}
    monkeypatch.setattr(doc_photos, "_ocr", lambda p: ocr[p.name])
    from app.core.parsers import PARSERS, image_parser

    monkeypatch.setattr(image_parser, "available", lambda: True)
    monkeypatch.setattr(file_agent, "_parsers", lambda: {**PARSERS, ".jpg": lambda p: chunk_located("image", 1, ocr[p.name])})
    monkeypatch.setattr(file_agent, "llm_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("model down")))
    return drive


def test_document_photos_are_found_by_their_text(pc):
    assert doc_photos.run_once() == 2
    assert doc_photos.progress() == {"photos": 2, "read": 2, "documents": 1}
    hits = doc_photos.search(["marksheet", "12"])
    assert [h["name"] for h in hits] == ["IMG-20250626-WA0122.jpg"]
    assert doc_photos.run_once() == 0  # nothing changed: nothing read again


def _drain(gen):
    events = []
    while True:
        try:
            events.append(next(gen))
        except StopIteration as stop:
            return events, stop.value


def test_a_jpg_question_looks_at_document_photos(pc):
    doc_photos.run_once()
    plan = file_agent.understand("my class 12 marksheet jpg")
    assert plan["exts"] == [".jpg", ".jpeg"]
    assert file_agent.candidates(plan)[0]["name"] == "IMG-20250626-WA0122.jpg"


def test_gather_skips_other_peoples_marksheets_and_reads_the_users(pc):
    doc_photos.run_once()
    _ev, found = _drain(file_agent.gather("what is my class 12 roll no", owner_name="Tarun Verma"))
    assert {c["name"] for c in found["chunks"]} == {"IMG-20250626-WA0122.jpg"}
    assert any("26139847" in c["text"] for c in found["chunks"])
    assert "Nitish Khunte" in [p for o in found["others"] for p in o["people"]]


# ---------- the whole chat, as in the screenshots ----------

@pytest.fixture
def chat(pc, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.assistant import chat_recall
    from app.db import assistant_db
    from app.main import app

    monkeypatch.setattr(settings, "db_path", tmp_path / "index.db")
    assistant_db.migrate()
    monkeypatch.setattr(chat_recall, "schedule_embed", lambda: None)
    monkeypatch.setattr(routes_chat, "hybrid_search", lambda *a, **k: {"results": [], "semantic": False})
    monkeypatch.setattr(routes_chat, "_recall", lambda m: [])

    def answer(prompt, chunks, history, **k):  # a stand-in model: reads the roll number of the first passage
        m = re.search(r"Roll No\.? :? ?(\d+)", " ".join(c["text"] for c in chunks))
        yield f"Your roll number is {m.group(1)} [{chunks[0]['name']}, page 1]." if m else "I couldn't find it in the provided documents."
        return False

    monkeypatch.setattr(routes_chat, "answer_stream", answer)
    client = TestClient(app, base_url="http://127.0.0.1:8756")
    state = {"conv": None}

    def ask(message):
        body = {"message": message, **({"conversation_id": state["conv"]} if state["conv"] else {})}
        text = client.post("/chat", json=body, headers={"Authorization": "Bearer test-token"}).text
        state["conv"] = json.loads(text.split("event: conversation\ndata: ")[1].split("\n")[0])["id"]
        reply = "".join(json.loads(line[6:])["delta"] for block in text.split("\n\n") if block.startswith("event: token")
                        for line in block.split("\n") if line.startswith("data: "))
        done = json.loads(text.split("event: done\ndata: ")[1].split("\n")[0])
        return reply, done

    return ask


def test_without_a_name_the_answer_says_whose_document_it_is(chat):
    reply, _ = chat("what is my class 12 roll no.")
    assert "in the name of" in reply and "my name is" in reply


def test_telling_the_name_re_asks_and_finds_the_users_photo(chat):
    doc_photos.run_once()
    chat("what is my class 12 roll no.")
    reply, done = chat("you fetch another persons marksheet my name is tarun verma")
    assert "26139847" in reply and "12626405" not in reply
    assert personalize.get_all()["owner_name"] == "Tarun Verma"
    reply, _ = chat("what is my class 12 roll no.")  # remembered from now on
    assert "26139847" in reply


def test_before_the_photos_are_read_it_says_so_instead_of_answering_from_a_friends_file(chat):
    personalize.update({"owner_name": "Tarun Verma"})
    reply, done = chat("what is my class 12 roll no.")
    assert "12626405" not in reply and "25071238" not in reply
    assert "other people's" in reply and "photo" in reply and "0 of 2" in reply
    assert done["citations"] == []


def test_a_name_on_its_own_is_acknowledged(chat):
    reply, done = chat("my name is tarun verma")
    assert reply.startswith("Got it, Tarun Verma") and done["route"] == "chat"


def test_a_not_found_answer_has_no_red_source(chat, monkeypatch):
    personalize.update({"owner_name": "Someone Else"})

    def nothing(prompt, chunks, history, **k):
        yield "I couldn't find a marksheet for you in the provided documents. (mark.pdf, page 1)"
        return False

    monkeypatch.setattr(routes_chat, "answer_stream", nothing)
    monkeypatch.setattr(routes_chat, "_owner_tag", lambda chunks, name: None)  # keep the files so the model is asked
    reply, done = chat("what is my class 12 roll no.")
    assert done["citations"] == []
