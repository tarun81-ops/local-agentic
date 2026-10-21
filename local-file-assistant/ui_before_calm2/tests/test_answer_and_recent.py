"""Browser tests: compact source rows under an answer, deleting recent chats, and folders in Find.
Same harness as test_ui_fixes.py (built ui/dist, every API call answered by a fake backend)."""
import json

import pytest

from test_ui_fixes import API, DEFAULTS, FakeBackend, page, site, stub  # noqa: F401 - fixtures

SNIPPET = ("CENTRAL BOARD OF SECONDARY EDUCATION SECONDARY SCHOOL EXAMINATION (CLASS X) 2021 MARKS STATEMENT CUM CERTIFICATE. "
           "This is to certify that TARUN VERMA Roll No. 12127198 Mother's Name SUNITA VERMA has achieved scholastic "
           "achievements as under. SUBJECT CODE SUBJECT THEORY PRACTICAL TOTAL POSITIONAL GRADE")
ANSWER = "Your class 10 roll no. is 12127198. (class 10.pdf, page 1)"
CITE = {"n": 1, "file": "class 10.pdf", "loc_kind": "page", "loc_no": 1, "span": [36, 58], "status": "checked", "verified": True,
        "path": "C:\\Users\\tarun verma\\OneDrive\\Documents\\class 10.pdf", "snippet": SNIPPET}


class Chats(FakeBackend):
    """Conversations that really go away when deleted."""

    def __init__(self, page, chats, **kw):
        self.chats = chats
        self.deleted = []
        super().__init__(page, **kw)

    def _handle(self, route):
        path = route.request.url.split("8756", 1)[1].split("?", 1)[0]
        if route.request.method == "DELETE" and path.startswith("/conversations/"):
            cid = int(path.rsplit("/", 1)[1])
            self.deleted.append(cid)
            self.chats[:] = [c for c in self.chats if c["id"] != cid]
            return route.fulfill(status=200, headers={**self._cors(), "Content-Type": "application/json"}, body='{"ok": true}')
        if route.request.method == "GET" and path == "/conversations":
            self.overrides["/conversations"] = {"conversations": list(self.chats)}
        if route.request.method == "GET" and path.startswith("/conversations/") and path[15:].isdigit():
            self.overrides[path] = {"id": int(path[15:]), "title": "roll no", "pinned": False, "messages": [
                {"id": 1, "role": "user", "content": "what is my class 10 roll no", "meta": {}, "created_at": 0},
                {"id": 2, "role": "assistant", "content": ANSWER, "meta": {"route": "files", "citations": [CITE], "uncited": []}, "created_at": 0, "rating": None},
            ]}
        return super()._handle(route)


def _chats():
    return [{"id": i, "title": t, "updated_at": 0, "pinned": False} for i, t in [(7, "class 10 roll no"), (8, "electricity bill"), (9, "offer letter")]]


def test_source_is_one_compact_row_with_the_answer_value_highlighted(page, site):
    Chats(page, _chats())
    page.add_init_script(stub())
    page.goto(f"{site}/index.html")
    page.click('.recent-item:has-text("class 10 roll no")')
    row = page.wait_for_selector(".src-row")
    assert page.locator(".src-row").count() == 1
    assert page.locator(".src-name").inner_text() == "class 10.pdf"
    assert page.locator(".src-ok").inner_text().strip() == "Verified"
    quote = page.locator(".src-quote").inner_text()
    assert "12127198" in quote and len(quote) < 200  # one line, not the whole page of text
    assert "12127198" in page.locator(".src-quote mark").all_inner_texts()
    assert row.bounding_box()["height"] < 110  # was a 260px-wide card about 230px tall
    assert page.locator(".answer-note").count() == 0 and page.locator(".uncited").count() == 0
    page.click('.src-quote .link-btn:has-text("MORE")')
    assert "SCHOLASTIC" in page.locator(".src-quote").inner_text().upper()
    page.click(".more-menu > summary")
    assert page.locator(".menu-item").all_inner_texts() == ["Remind me about it", "Study it (flashcards / quiz)", "Copy file path"]
    assert page.errors == []


def test_folder_button_shows_the_source_in_explorer(page, site):
    fake = Chats(page, _chats())
    page.add_init_script(stub())
    page.goto(f"{site}/index.html")
    page.click('.recent-item:has-text("class 10 roll no")')
    page.click('.src-actions button:has-text("Show in folder")')
    page.wait_for_timeout(300)
    assert fake.count("/find/reveal") == 1
    assert page.errors == []


def test_delete_one_recent_chat_and_the_open_one_starts_fresh(page, site):
    fake = Chats(page, _chats())
    page.add_init_script(stub())
    page.goto(f"{site}/index.html")
    page.click('.recent-item:has-text("class 10 roll no")')
    page.wait_for_selector(".src-row")
    page.hover('.recent-row:has-text("electricity bill")')
    page.click('[aria-label="Delete chat: electricity bill"]')
    page.locator('.recent-row:has-text("electricity bill")').wait_for(state="detached")
    assert fake.deleted == [8]
    assert page.locator(".src-row").count() == 1  # deleting another chat leaves this one open
    page.click('[aria-label="Delete chat: class 10 roll no"]')
    page.locator('.recent-row:has-text("class 10 roll no")').wait_for(state="detached")
    page.wait_for_timeout(300)
    assert fake.deleted == [8, 7]
    assert page.locator(".src-row").count() == 0  # the chat on screen was deleted: a new chat starts
    assert page.errors == []


def test_clear_all_asks_first_then_deletes_every_chat(page, site):
    fake = Chats(page, _chats())
    page.add_init_script(stub())
    page.goto(f"{site}/index.html")
    page.wait_for_selector(".recent-clear")
    page.once("dialog", lambda d: d.dismiss())
    page.click(".recent-clear")
    page.wait_for_timeout(300)
    assert fake.deleted == [] and page.locator(".recent-row").count() == 3
    page.once("dialog", lambda d: d.accept())
    page.click(".recent-clear")
    page.locator(".recent-row").first.wait_for(state="detached")
    assert sorted(fake.deleted) == [7, 8, 9]
    assert page.errors == []


def test_find_shows_a_folder_and_opens_it(page, site):
    folder = {"path": "C:\\Users\\tarun verma\\OneDrive\\Documents\\Blackmagic Design", "name": "Blackmagic Design",
              "folder": "C:\\Users\\tarun verma\\OneDrive\\Documents", "ext": "/", "is_folder": True, "size": None,
              "mtime": 0, "modified": "02 Mar 2026", "snippet": "", "loc": None, "score": 0.65, "why": ["name has “blackmagic”"]}
    fake = FakeBackend(page, overrides={"/find": {"results": [folder], "understood": {"words": ["blackmagic"], "exts": ["/"], "type_label": "FOLDER", "date_label": "", "recent": False},
                                                  "relaxed": [], "pc_access": True, "semantic": False, "ms": 12}})
    page.add_init_script(stub())
    page.goto(f"{site}/index.html")
    page.click('.nav-item:has-text("FIND FILE")')
    page.fill("#find-input", "blackmagic folder")
    page.press("#find-input", "Enter")
    page.wait_for_selector(".find-row")
    assert page.locator(".find-row .swatch").inner_text() == "DIR"
    assert "Folder · 02 Mar 2026" in page.locator(".find-facts").inner_text()
    assert page.locator(".page-meta").first.inner_text().startswith("1 folder ")
    page.click('.find-row button:has-text("OPEN FOLDER")')
    page.wait_for_timeout(300)
    assert fake.count("/find/open") == 1
    assert page.errors == []


def test_labels_are_sentence_case_not_all_caps(page, site):
    """The calm redesign: no UI label shouts. Allowed: acronyms (PC, PDF, RAM…) and text from your files."""
    Chats(page, _chats())
    page.add_init_script(stub())
    page.goto(f"{site}/index.html")
    page.click('.recent-item:has-text("class 10 roll no")')
    page.wait_for_selector(".src-row")
    script = """() => [...document.querySelectorAll('#sidebar *, .page-head *, .src-head *, .feedback *, .related *, .ask-box *')]
        .filter((n) => n.childNodes.length && [...n.childNodes].some((c) => c.nodeType === 3 && c.textContent.trim()))
        .map((n) => [...n.childNodes].filter((c) => c.nodeType === 3).map((c) => c.textContent).join('').trim())"""
    texts = page.evaluate(script)
    import re
    shouting = [t for t in texts if re.search(r"\\b(?!PC\\b|PDF\\b|RAM\\b|AI\\b)[A-Z]{3,}\\b.*\\b[A-Z]{3,}\\b", t)]
    assert texts and shouting == [], shouting
    assert page.errors == []
