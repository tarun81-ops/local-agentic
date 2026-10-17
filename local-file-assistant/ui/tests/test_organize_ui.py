"""Browser tests for Organize: Move & delete (browse, select, move/copy via the folder picker, rename,
new folder, delete with a confirm step), Clean up options and the History tab. A fake backend keeps a
tiny file tree in memory, so moves really change what the next listing shows."""
import json
import time
from urllib.parse import parse_qs

from test_ui_fixes import API, DEFAULTS, page, site, stub  # noqa: F401 - fixtures

DOCS = "C:\\Users\\me\\Documents"
DL = "C:\\Users\\me\\Downloads"
PLACES = [{"label": "Downloads", "path": DL, "kind": "folder"}, {"label": "Documents", "path": DOCS, "kind": "folder"}]
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "*"}


class FakeFS:
    def __init__(self, page):
        self.tree = {
            DL: [("bill.pdf", False, 52000), ("photo.jpg", False, 1200), ("Old stuff", True, None)],
            DOCS: [("Project", True, None), ("notes.txt", False, 300)],
            DL + "\\Old stuff": [],
            DOCS + "\\Project": [],
        }
        self.applied: list[list[dict]] = []
        self.calls: list[tuple[float, str, dict]] = []
        self.history: list[dict] = []
        page.route(f"{API}/**", self._handle)

    def _ok(self, route, body):
        route.fulfill(status=200, headers={**CORS, "Content-Type": "application/json"}, body=json.dumps(body))

    def _listing(self, path):
        entries = [{"name": n, "path": f"{path}\\{n}", "is_dir": d, "size": s, "mtime": 1790000000, "ext": "" if d else "." + n.rsplit(".", 1)[-1],
                    "runnable": (not d) and n.lower().endswith((".exe", ".bat", ".lnk"))}
                   for n, d, s in sorted(self.tree.get(path, []), key=lambda x: (not x[1], x[0].lower()))]
        parts = path.split("\\")
        crumbs = [{"name": parts[i], "path": "\\".join(parts[: i + 1])} for i in range(2, len(parts))]
        return {"path": path, "parent": "\\".join(parts[:-1]) if len(parts) > 3 else None, "crumbs": crumbs, "entries": entries, "more": 0, "hidden": 0}

    def _apply(self, actions):
        self.applied.append(actions)
        results = []
        for a in actions:
            parent, _, name = a["path"].rpartition("\\")
            item = next((x for x in self.tree.get(parent, []) if x[0] == name), None)
            if a["op"] in ("move", "delete", "rename") and item:
                self.tree[parent].remove(item)
            if a["op"] in ("move", "copy") and item:
                self.tree.setdefault(a.get("to_folder") or a["to"].rpartition("\\")[0], []).append(item)
            if a["op"] == "rename" and item:
                self.tree[parent].append((a["new_name"], item[1], item[2]))
            if a["op"] == "mkdir":
                self.tree.setdefault(parent, []).append((name, True, None))
                self.tree[a["path"]] = []
            results.append({"op": a["op"], "path": a["path"], "to": a.get("to_folder"), "ok": True})
        batch = f"b{len(self.applied)}"
        undoable = any(a["op"] != "delete" for a in actions)
        self.history.insert(0, {"batch": batch, "at": "2026-10-15T15:00:00+00:00", "moves": sum(a["op"] == "move" for a in actions), "copies": 0, "folders": 0,
                                "trashed": sum(a["op"] == "delete" for a in actions), "undoable": int(undoable),
                                "entries": [{"from": a["path"], "to": None, "op": a["op"]} for a in actions]})
        return {"batch": batch, "results": results}

    def _handle(self, route):
        req = route.request
        if req.method == "OPTIONS":
            return route.fulfill(status=204, headers=CORS)
        path, _, query = req.url.split("8756", 1)[1].partition("?")
        body = json.loads(req.post_data) if req.post_data else {}
        self.calls.append((time.monotonic(), path, body))
        if path == "/organize/places":
            return self._ok(route, {"places": PLACES, "pc_access": True})
        if path == "/organize/browse":
            return self._ok(route, self._listing(parse_qs(query)["path"][0]))
        if path == "/organize/apply":
            return self._ok(route, self._apply(body["actions"]))
        if path == "/organize/history":
            return self._ok(route, {"batches": self.history})
        if path == "/organize/undo":
            return self._ok(route, {"results": [{"op": "move", "from": "x", "to": "y", "ok": True}]})
        if path == "/organize/tidy/folders":
            return self._ok(route, {"folders": [{"key": "downloads", "label": "Downloads", "path": DL}]})
        if path == "/organize/tidy":
            return self._ok(route, {"summary": "1 file to sort into folders.", "root": DL, "scanned": 3, "kind": "tidy", "freed_bytes": 0, "dropped_invalid": 0,
                                    "by_type": [{"type": "Documents", "bytes": 52000, "count": 1}, {"type": "Images", "bytes": 1200, "count": 1}],
                                    "actions": [{"op": "move", "path": DL + "\\bill.pdf", "to": DL + "\\Documents\\bill.pdf", "group": "Sort by type/Documents",
                                                 "selected": True, "size": 52000, "reason": ".pdf file"}]})
        return self._ok(route, DEFAULTS.get(path, {}))

    def bodies(self, path):
        return [b for _, p, b in self.calls if p == path]


def _start(page, site, tab):
    fs = FakeFS(page)
    page.add_init_script(stub())
    page.add_init_script("try { localStorage.clear(); } catch (e) {}")
    page.goto(f"{site}/index.html")
    page.click('.nav-item:has-text("Organize")')
    page.click(f"#organize-tab-{tab}")
    if tab == "files":
        page.wait_for_selector('.fm-row:has-text("bill.pdf")')
    return fs


def _row(page, name):
    return page.locator(f'.fm-list .fm-row:has-text("{name}")')


def _names(page):
    return page.locator(".fm-list .fm-row .fm-name").all_inner_texts()


def test_move_two_files_with_the_folder_picker_and_undo(page, site):
    fs = _start(page, site, "files")
    assert _names(page) == ["Old stuff", "bill.pdf", "photo.jpg"]  # folders first
    _row(page, "bill.pdf").click()
    _row(page, "photo.jpg").click(modifiers=["Control"])
    assert page.locator(".fm-selbar .org-count").inner_text().startswith("2 items selected")
    page.click('.fm-selbar button:has-text("Move to…")')
    page.wait_for_selector(".org-dialog")
    page.wait_for_timeout(200)
    assert page.locator('.org-dialog button:has-text("Move here")').is_disabled()  # they are already here
    page.click('.org-dialog .chip:has-text("Documents")')
    page.wait_for_selector('.org-dialog .fm-row:has-text("Project")')
    page.click('.org-dialog button:has-text("Move here")')
    page.wait_for_selector("text=2 items moved")
    assert fs.applied[-1] == [{"op": "move", "path": f"{DL}\\bill.pdf", "to_folder": DOCS}, {"op": "move", "path": f"{DL}\\photo.jpg", "to_folder": DOCS}]
    assert _names(page) == ["Old stuff"]  # the listing was refreshed
    page.click('.org-toast button:has-text("Undo")')
    page.wait_for_selector("text=Undone.")
    assert fs.bodies("/organize/undo")[-1] == {"batch": "b1"}
    assert page.errors == []


def test_delete_asks_first_and_goes_to_the_recycle_bin(page, site):
    fs = _start(page, site, "files")
    _row(page, "Old stuff").click()
    page.locator(".fm-list").focus()
    page.keyboard.press("Delete")
    page.wait_for_selector("text=Send “Old stuff” to the Recycle Bin? Folders go with everything inside them.")
    page.click('.fm-selbar button:has-text("Cancel")')
    assert fs.applied == []
    page.click('.fm-selbar button:has-text("Delete")')
    page.click('.fm-selbar button:has-text("Delete")')  # the confirm step
    page.wait_for_selector("text=1 item sent to the Recycle Bin")
    assert fs.applied == [[{"op": "delete", "path": f"{DL}\\Old stuff"}]]
    assert page.locator('.org-toast button:has-text("Undo")').count() == 0  # a delete is undone from the Recycle Bin
    assert page.errors == []


def test_rename_new_folder_and_copy_into_a_sub_folder(page, site):
    fs = _start(page, site, "files")
    _row(page, "bill.pdf").click()
    page.click('.fm-selbar button:has-text("Rename")')
    box = page.locator(".fm-rename")
    assert box.evaluate("(i) => [i.selectionStart, i.selectionEnd]") == [0, 4]  # the name, not ".pdf"
    box.fill("electricity bill.pdf")
    box.press("Enter")
    page.wait_for_selector('.fm-row:has-text("electricity bill.pdf")')
    assert fs.applied[-1] == [{"op": "rename", "path": f"{DL}\\bill.pdf", "new_name": "electricity bill.pdf"}]
    page.click('.fm-toolbar button:has-text("New folder")')
    page.locator(".fm-rename").press("Enter")  # keep the suggested name
    page.wait_for_selector('.fm-list .fm-row:has-text("New folder")')
    assert fs.applied[-1] == [{"op": "mkdir", "path": f"{DL}\\New folder"}]
    _row(page, "photo.jpg").click()
    page.click('.fm-selbar button:has-text("Copy to…")')
    page.click('.org-dialog .chip:has-text("Documents")')
    page.wait_for_selector('.org-dialog .fm-row:has-text("Project")')
    page.click('.org-dialog .fm-row:has-text("Project")')
    page.wait_for_selector('.org-dialog .fm-crumb:has-text("Project")')
    page.click('.org-dialog button:has-text("Copy here")')
    page.wait_for_selector("text=1 item copied")
    assert fs.applied[-1] == [{"op": "copy", "path": f"{DL}\\photo.jpg", "to_folder": f"{DOCS}\\Project"}]
    assert page.errors == []


def test_double_click_opens_folders_path_bar_goes_back_and_filter_narrows(page, site):
    _start(page, site, "files")
    _row(page, "Old stuff").dblclick()
    page.wait_for_selector("text=This folder is empty.")
    page.click('.fm-crumb:has-text("Downloads")')
    page.wait_for_selector('.fm-row:has-text("bill.pdf")')
    page.fill(".fm-filter", "pho")
    assert _names(page) == ["photo.jpg"]
    assert page.errors == []


def test_clean_up_sends_only_the_chosen_checks_and_shows_what_takes_space(page, site):
    fs = _start(page, site, "clean")
    page.click('.org-checks .chip:has-text("Old large files")')  # switch it off
    assert page.locator(".org-thresholds").is_hidden()
    page.click('button:has-text("Find clutter")')
    page.wait_for_selector(".type-bars")
    assert fs.bodies("/organize/tidy")[-1]["checks"] == ["duplicates", "partial", "empty", "sort"]
    assert page.locator(".type-name").all_inner_texts() == ["Documents", "Images"]
    assert page.locator(".org-actionbar .org-count").inner_text() == "1 change selected"
    page.click('.org-actionbar button:has-text("Apply")')
    page.wait_for_selector("text=1 item moved")
    assert page.errors == []


def test_history_tab_lists_changes_with_undo(page, site):
    fs = _start(page, site, "files")
    _row(page, "bill.pdf").click()
    page.click('.fm-selbar button:has-text("Move to…")')
    page.click('.org-dialog .chip:has-text("Documents")')
    page.wait_for_selector('.org-dialog .fm-row:has-text("Project")')
    page.click('.org-dialog button:has-text("Move here")')
    page.wait_for_selector("text=1 item moved")
    page.click("#organize-tab-history")
    page.wait_for_selector(".history-row")
    assert "bill.pdf" in page.locator(".history-row .folder-meta").inner_text()
    page.click('.history-row button:has-text("Undo")')
    page.wait_for_timeout(300)
    assert fs.bodies("/organize/undo")[-1] == {"batch": "b1"}
    assert page.errors == []


def test_a_running_change_blocks_other_actions_and_says_so(page, site):
    """Review #4: while a slow move ran, the buttons stayed live and a second change could start."""
    fs = FakeFS(page)
    page.add_init_script(stub(delays={"/organize/apply": 1.2}))
    page.add_init_script("try { localStorage.clear(); } catch (e) {}")
    page.goto(f"{site}/index.html")
    page.click('.nav-item:has-text("Organize")')
    page.click("#organize-tab-files")
    page.wait_for_selector('.fm-row:has-text("bill.pdf")')
    _row(page, "bill.pdf").click()
    page.click('.fm-selbar button:has-text("Move to…")')
    page.click('.org-dialog .chip:has-text("Documents")')
    page.wait_for_selector('.org-dialog .fm-row:has-text("Project")')
    page.click('.org-dialog button:has-text("Move here")')
    page.wait_for_selector("text=Moving… Large files can take a while.")
    assert page.locator('.fm-selbar button:has-text("Delete")').count() == 0
    assert page.locator(".fm-list").get_attribute("aria-busy") == "true"
    page.locator(".fm-list").focus()
    page.keyboard.press("Delete")  # ignored while busy
    assert page.locator(".fm.is-busy .fm-places").evaluate("(n) => getComputedStyle(n).pointerEvents") == "none"
    page.wait_for_selector("text=1 item moved")
    assert len(fs.applied) == 1 and page.locator(".fm-list").get_attribute("aria-busy") is None
    assert page.errors == []


def test_double_clicking_a_program_shows_it_instead_of_running_it(page, site):
    """Review #2: double-click opened .exe/.bat/.lnk files, which runs them."""
    fs = FakeFS(page)
    fs.tree[DL].append(("setup.exe", False, 900))
    page.add_init_script(stub())
    page.add_init_script("try { localStorage.clear(); } catch (e) {}")
    page.goto(f"{site}/index.html")
    page.click('.nav-item:has-text("Organize")')
    page.click("#organize-tab-files")
    page.wait_for_selector('.fm-row:has-text("setup.exe")')
    _row(page, "setup.exe").dblclick()
    page.wait_for_selector("text=setup.exe is a program or script, so it isn’t opened from here.")
    page.wait_for_timeout(200)
    assert fs.bodies("/organize/reveal") == [{"path": f"{DL}\\setup.exe"}] and fs.bodies("/organize/open") == []
    assert page.errors == []


def test_clean_up_says_applying_while_changes_run(page, site):
    """Second review: Clean up's Apply showed nothing while it ran."""
    FakeFS(page)
    page.add_init_script(stub(delays={"/organize/apply": 1.0}))
    page.add_init_script("try { localStorage.clear(); } catch (e) {}")
    page.goto(f"{site}/index.html")
    page.click('.nav-item:has-text("Organize")')
    page.click("#organize-tab-clean")
    page.click('button:has-text("Find clutter")')
    page.wait_for_selector(".type-bars")
    page.click('.org-actionbar button:has-text("Apply")')
    page.wait_for_selector("text=Applying… Large files can take a while.")
    assert page.locator('button:has-text("Find clutter")').is_disabled()
    page.wait_for_selector("text=1 item moved")
    assert page.errors == []
