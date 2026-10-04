"""Personalization core: validation, prompt injection bounds, and that nothing breaks old callers."""
import pytest
from fastapi.testclient import TestClient

from app.core import personalize
from app.core.assistant import prompts
from app.core.llm import answerer
from app.core.llm.verifier import STRICT_THRESHOLD, THRESHOLD, verify
from app.main import app

AUTH = {"Authorization": "Bearer test-token"}
CHUNKS = [{"path": "C:/x/report.pdf", "name": "report.pdf", "loc_kind": "page", "loc_no": 2, "text": "Revenue was 500 in Raipur.", "snippet": ""}]


@pytest.fixture(autouse=True)
def _reset():
    personalize.update(personalize.DEFAULTS)
    yield
    personalize.update(personalize.DEFAULTS)


def test_defaults():
    got = personalize.get_all()
    assert got["answer_style"] == "concise" and got["language"] == "auto" and got["cite_pages"] is True
    assert got["verifier_strict"] == "normal" and got["memory_review"] is True


def test_partial_update_keeps_the_rest():
    personalize.update({"answer_style": "study"})
    personalize.update({"language": "hinglish"})
    got = personalize.get_all()
    assert got["answer_style"] == "study" and got["language"] == "hinglish"


@pytest.mark.parametrize("patch", [
    {"answer_style": "poetic"}, {"language": "klingon"}, {"cite_pages": "yes"},
    {"verifier_strict": "max"}, {"profile": "x" * 801}, {"profile": 5}, {"nope": 1}, {"folder_profiles": []},
])
def test_invalid_values_are_rejected_and_change_nothing(patch):
    with pytest.raises(ValueError):
        personalize.update({"answer_style": "detailed", **patch})
    assert personalize.get_all()["answer_style"] == "concise"


def test_profile_cap_boundary():
    personalize.update({"profile": "x" * 800})
    assert len(personalize.profile()) == 800


def test_routes():
    c = TestClient(app, base_url="http://127.0.0.1:8756")
    assert c.get("/personalize").status_code == 401
    assert c.put("/personalize", json={"answer_style": "simple"}, headers=AUTH).json()["answer_style"] == "simple"
    bad = c.put("/personalize", json={"answer_style": "x"}, headers=AUTH)
    assert bad.status_code == 400 and "answer_style" in bad.json()["detail"]
    assert c.get("/personalize", headers=AUTH).json()["answer_style"] == "simple"


def test_style_instruction_is_short_in_every_combination():
    for s in personalize.STYLES:
        for lang in personalize.LANGUAGES:
            assert len(prompts.style_instruction(s, lang, True).split()) <= 40


def test_profile_is_labelled_truncated_and_never_a_citation():
    out = prompts.with_profile("question?", "I study ETE " + "y" * 2000)
    assert "cite it as a file" in out and out.endswith("question?")
    assert len(out) < prompts.PROFILE_MAX + 200
    assert prompts.with_profile("q", "  ") == "q"
    # the verifier only accepts file-shaped citations, so profile text can't become one
    assert verify("You study ETE (profile, page 1).", CHUNKS)["citations"] == []


def test_old_signatures_still_work(monkeypatch):
    seen = {}
    monkeypatch.setattr(answerer, "_stream", lambda messages, model: (seen.setdefault("m", messages), (yield "ok"))[1])
    list(answerer.chat_stream("hi"))
    assert seen["m"][0]["content"] == prompts.CHAT_SYSTEM
    seen.clear()
    list(answerer.answer_stream("q", CHUNKS))
    assert seen["m"][-1]["content"].endswith("Question: q\n")


@pytest.mark.parametrize("style", personalize.STYLES)
def test_style_goes_into_both_prompts_and_citations_still_verify(monkeypatch, style):
    seen = []
    monkeypatch.setattr(answerer, "_stream", lambda messages, model: (seen.append(messages), (yield "ok"))[1])
    line = prompts.style_instruction(style, "auto", True)
    list(answerer.answer_stream("q", CHUNKS, style=line))
    list(answerer.chat_stream("q", style=line))
    assert line in seen[0][-1]["content"] and line in seen[1][0]["content"]
    assert "cite" in seen[0][-1]["content"].lower()
    assert verify("Revenue was 500 (report.pdf, page 2).", CHUNKS)["all_verified"]


def test_added_prompt_text_stays_under_budget():
    extra = len(prompts.style_instruction("study", "hinglish", True)) + len(prompts.with_profile("", "x" * 800))
    assert extra < 1200  # ~300 tokens: fine for a 2B model


def test_strict_threshold_is_stricter():
    answer = "Revenue was 500 and Mumbai grew (report.pdf, page 2)."  # 2 of 3 facts in the source
    assert verify(answer, CHUNKS, THRESHOLD)["all_verified"]
    assert not verify(answer, CHUNKS, STRICT_THRESHOLD)["all_verified"]


def test_per_request_style_and_language_override_the_saved_default(monkeypatch):
    from app.api import routes_chat

    seen = []

    def fake_chat(message, history=None, model=None, style=""):
        seen.append((message, style))
        yield "ok"
        return False

    monkeypatch.setattr(routes_chat, "chat_stream", fake_chat)
    monkeypatch.setattr(routes_chat.ram, "status", lambda: {"low": False, "free_mb": None})
    personalize.update({"answer_style": "concise", "profile": "I study ETE"})
    with TestClient(app, base_url="http://127.0.0.1:8756") as c:  # startup migrates the database
        c.post("/chat", json={"message": "hello there", "mode": "chat", "style": "study", "language": "hinglish"}, headers=AUTH)
        c.post("/chat", json={"message": "hello again", "mode": "chat", "style": "bogus"}, headers=AUTH)
    (m1, s1), (m2, s2) = seen
    assert "self-check" in s1 and "Hinglish" in s1
    assert "brief" in s2 and "language the user writes in" in s2  # unknown style falls back to the saved one
    assert "I study ETE" in m1 and m1.endswith("hello there")


def test_appearance_defaults_partial_update_and_validation():
    assert personalize.get_all()["appearance"] == {"theme": "system", "accent": "red", "font_scale": 1.0, "overlay_compact": False, "overlay_position": "top-right"}
    personalize.update({"appearance": {"theme": "dark"}})
    personalize.update({"appearance": {"accent": "violet", "font_scale": 1.2}})
    got = personalize.get_all()["appearance"]
    assert (got["theme"], got["accent"], got["font_scale"], got["overlay_compact"]) == ("dark", "violet", 1.2, False)
    for bad in ({"theme": "neon"}, {"accent": "pink"}, {"font_scale": 1.31}, {"font_scale": 0.89}, {"font_scale": True}, {"overlay_compact": "yes"}, {"overlay_position": "left"}, {"colour": "red"}):
        with pytest.raises(ValueError):
            personalize.update({"appearance": bad})
    assert personalize.get_all()["appearance"]["theme"] == "dark"  # a rejected change changes nothing


def test_study_questions_and_profile_wording_keep_the_profile_out_of_the_content():
    """Found live: with About me set, the 2B model wrote its self-check questions about the profile."""
    study = prompts.style_instruction("study", "auto", True)
    assert "about the topic of this answer, never about the user" in study
    assert len(study.split()) <= 40
    framed = prompts.with_profile("question?", "First-year ETE student in Raipur.")
    assert "not the topic" in framed and "never ask about it" in framed and "cite it as a file" in framed
