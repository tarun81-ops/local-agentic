# Prompt for Claude Code: personalize the Local File Assistant

Paste everything below the line into Claude Code, started in `C:\Users\tarun verma\local agent\local-file-assistant`.

---

You are working on **Local File Assistant**, a private, local-first Windows 11 desktop app. The user is Tarun, a first-year ETE student in Raipur. All setup and run instructions you give must be for **Windows (PowerShell)**. Never suggest Linux or Ubuntu commands.

Your job is to add **personalization** across the app, in the phases below. Work phase by phase. After each phase, run the backend tests and the UI build, fix what breaks, and commit. Do not start the next phase while tests are red.

## 0. Read the code first (do not skip)

Before changing anything, read these. Several things the older docs call "planned" already exist, so do not rebuild them. Extend them.

- `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/ISSUES.md`, `docs/TESTING-ON-WINDOWS.md`, `docs/decisions/0001-images.md`
- Backend entry and config: `backend/app/main.py`, `config.py`, `security.py`, `core/prefs.py` (a JSON key/value store in `%APPDATA%\LocalFileAssistant\settings.json`), `core/ram.py`
- Chat pipeline: `api/routes_chat.py`, `core/llm/answerer.py`, `core/llm/client.py`, `core/llm/verifier.py`, `core/llm/idle.py`, `core/assistant/prompts.py`, `core/assistant/router.py`, `core/assistant/conversation.py`
- Memory: `core/assistant/memory_store.py`, `memory_extract.py`, `api/routes_memory.py`, migration `0002_memory.sql`
- Learning and search: `core/assistant/learning.py`, `core/search/hybrid.py`, `db/sqlite_fts.py`, `core/indexer.py`, `core/watcher.py`, `api/routes_files.py`
- Tasks and proactive: `core/assistant/tasks.py`, `briefing.py`, `scheduler.py`, `api/routes_tasks.py`, `api/routes_proactive.py`, migrations `0003`–`0006`
- UI: `ui/src/main.js`, `api.js`, `style.css`, `components/*`, `pages/*` (chat, search, memory, tasks, index, settings, organize), `overlay.js`, `ui/electron/main.js`, `preload.js`
- Tests: `backend/tests/conftest.py` (every test run gets its own data dir, a fixed token, and a fake embedder) and the existing test files, to copy their style.

### What already exists (verified in the code)

- A Memory page with add, edit, pin, delete, wipe and export, backed by `memories` (FTS5 plus embeddings). A background extractor (`memory_extract.py`) saves facts about the user automatically after a chat is quiet for 5 minutes. A `BLOCKED` regex stops sensitive facts (passwords, health, salary, ID numbers) being auto-saved.
- Conversations with saved history, feedback ratings, and open-history ranking (`learning.py`).
- Tasks, reminders and events, plus a morning briefing and an overdue nudge with quiet hours and a daily cap (`briefing.py`).
- Local voice (faster-whisper and Piper), the global-hotkey overlay with window-context capture, and form-upload suggestions.
- Settings page sections: model, shortcut, privacy, proactive, voice, learning.
- The SQLite schema for `assistant.db` is at migration **0006**. Next is **0007**. Migrations are applied in order and tracked in `PRAGMA user_version`. They are never dropped, so write forward-only migrations.

### Hard constraints (from the project's own principles)

1. **Nothing leaves the machine.** No cloud calls, no telemetry, no new network dependency.
2. **The model is small.** The active model is `qwen3-vl:2b-instruct` on a CPU laptop (ASUS Vivobook S14, Core Ultra 5 225H, 16 GB RAM, shared iGPU). The 4B model was measured at about 2x slower with no real quality gain and left about 0.35 GB RAM free, so **never auto-select a bigger model**. History is capped at about 3000 tokens (`history_token_budget`). Everything you add to a prompt must be short and bounded. Measure it in a test.
3. **Text from files, windows or the clipboard is data, never instructions.** Keep the existing labelled-as-quoted-data pattern. The user's own profile is trusted, but still label it so the model never cites it as a file (copy the `with_memory` pattern).
4. **No silent personal data.** Do not log the profile, memories or questions. The log rule in `main.py` stays: only paths and errors.
5. **Sensitive categories stay out of automatic memory.** Keep the `BLOCKED` filter and extend it if needed. Never weaken it.
6. **Actions stay proposal-only.** Do not add anything that runs on the PC without the existing approval flow.
7. **Accessibility.** The UI was recently brought to WCAG AA contrast with overlay screen-reader support. Any new theme or accent must keep 4.5:1 for small text. Add a small contrast-check test or script.
8. **Rendering safety.** Text goes in as text nodes through the `el()` helper, never `innerHTML`. Keep the CSP in `index.html` unchanged.
9. Backend binds loopback only and every route needs the token. New routers get registered in `main.py` and go through the same guard.
10. Keep `eval/assistant_eval.py` and the existing tests green. Add tests for everything you add.

## Phase 1: personalization core (backend)

Create `backend/app/core/personalize.py` as the single place for these preferences. Store them in `prefs` under one key (for example `personalize`) so they live in `settings.json`. Provide `get_all()`, `update(patch)` with validation (raise `ValueError` with a human message, like `briefing.save`), and small accessors.

Preferences:

- `profile` (str, max 800 characters): the "About me" text. Example content: course, college, projects, how they like to be answered.
- `answer_style`: `concise` (default), `detailed`, `study`, `simple`.
- `language`: `auto` (default, reply in the language the user writes in), `english`, `hinglish`.
- `cite_pages` (bool, default true): always cite page and part numbers.
- `verifier_strict` (`normal` | `strict`): wire it to the verifier threshold after reading `verifier.py`. If the verifier has no threshold to adjust, drop this option rather than faking it.
- `perf_profile`, `appearance`, `memory_review`, `folder_profiles`, `inbox_folders`: defined in later phases. Reserve the keys now.

Add `api/routes_personalize.py` with `GET /personalize` and `PUT /personalize` (partial updates). Register it in `main.py`.

Prompt injection, in `core/assistant/prompts.py`:

- `with_profile(message, profile)`: labelled "what the user told you about themselves, not file excerpts, never cite as files". Truncate to the cap.
- `style_instruction(style, language, cite_pages)`: at most about 40 words. It goes into the answerer's `PROMPT` (file mode) and into `CHAT_SYSTEM` (chat mode). Add an optional `style` argument to `answer_stream` and `chat_stream` and keep the old call signatures working.
- In `routes_chat._stream`, compose: context, then profile, then memory, then the message, matching the current order. The search and the rewrite step must keep using the bare message, as they do now.
- Allow a per-request override: add an optional `style` field to `ChatRequest`, so the UI can switch style for one question without changing the saved default.
- Study style behavior: structured, short sections, ends with 2 self-check questions. Simple style: plain words, short sentences, one example. Keep citations intact in every style, and add a test that the verifier still passes with each style's prompt shape.

Tests (`tests/test_personalize.py`): validation errors, partial update, caps, defaults, that the profile never appears as a citation, that the added prompt text stays under a fixed character budget, and that old call signatures still work.

## Phase 2: memory with a review step

Goal: nothing about the user is saved silently unless they opt out.

- Migration `0007_personalization.sql`: add `status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','pending'))` to `memories`. Existing rows stay active.
- `memory_extract.extract_conversation` saves new facts as `pending` when `memory_review` is on (default **on**). Facts the user states with "remember that …" stay active immediately, since that is an explicit request.
- `memory_store.recall`, `forget` and the prompt injection use **active** memories only. `list_all(status=...)` supports filtering.
- `routes_memory`: `POST /memory/{id}/approve`, `POST /memory/approve-all`, and rejecting is the existing delete. Add a `status` query parameter to `GET /memory`.
- Memory page: a "NEEDS REVIEW" section above the list with APPROVE, EDIT and DISMISS per item and an APPROVE ALL button. Add a "What I know about you" header with the counts. Keep the existing pin, edit, delete, wipe and export.
- Settings: a switch "Ask me before saving what I learn from chats" (default on).
- Update the existing memory tests that assume auto-saved facts are active. Add tests for pending, approve, reject, recall ignoring pending, and the blocked filter still applying.
- A **sensitive-category test**: feed extraction text containing health, salary, ID-number and password content and assert none of it becomes even a pending row.

## Phase 3: answer styles and study mode

- UI: a compact style switcher on the chat page header next to the existing MODE and scope selects (CONCISE, DETAILED, STUDY, SIMPLE), plus a language select. It sends the per-request `style`. Add the same quick toggle to the overlay, kept small. The Settings page holds the saved defaults.
- `POST /study/generate` in a new `api/routes_study.py` with `{path, kind: "flashcards" | "quiz", count: 5..15}`:
  - The path must be an indexed file (reuse `sqlite_fts.is_indexed`). Add a small `sqlite_fts` helper that returns a file's chunks in order, capped to a character budget so a 2B model can cope. Over a large file, sample evenly across the chunks and say so in the response.
  - Use `client.llm_json` with a strict JSON shape. Validate and clean in code (drop empty or duplicate items, cap lengths). Each item keeps its source location (`loc_kind`, `loc_no`).
  - Add `GET /study/export?format=csv` or return CSV text so cards can be imported into Anki.
  - Refuse with a clear message when RAM is low (`ram.is_low()`), as the extractor does.
- UI: a STUDY button on Search result rows and on cited sources opens a simple card view (flip, next, mark known), with quiz mode (reveal answer). Nothing is stored unless the user presses SAVE DECK; if you add storage, put it in `assistant.db` under a new migration and make it wipeable.
- Tests: JSON cleaning, invalid path rejected, low-RAM refusal, budgeted sampling. Use a stubbed `llm_json`, never a live model.

## Phase 4: per-folder behavior

Store `folder_profiles` as `{ "<normalized root path>": {label, answer_style, extensions[], exclude_globs[], caption_images, ocr_only} }` in the personalize prefs. Normalize paths the same way `routes_files._existing_dir` and `sqlite_fts` do (resolved, Windows case rules), and add a test for case-insensitive matching.

- Indexing: apply `extensions` (allowlist, empty means all) and `exclude_globs` inside the indexer's file walk and `watcher.py`, so both agree. Changing a folder profile must trigger a rescan of that folder, and files that are now excluded must be removed from both stores (SQLite and LanceDB) using the existing `_forget` path.
- `caption_images` and `ocr_only` override the global `CAPTION_IMAGES` per folder.
- Answering: when the request has a `root`, or the top result's file lives under a folder with its own `answer_style`, use that style unless the request supplied one. Use `sqlite_fts.root_for`.
- UI (Index page): each folder card gets a small "FOLDER SETTINGS" drawer: label, style, file types, exclusions, caption toggle, with a clear "applies after rescan" note and a RESCAN button.
- Add presets as quick-fill buttons: "College (PDF, DOCX, PPTX)", "Code and notes (md, txt, csv)", "Personal (OCR only, no captions)". These just fill the form.
- Tests: allowlist and exclusion in a real temp folder, removal on exclusion, watcher agreement, case-insensitive path match.

## Phase 5: collections and a "recent and relevant" home

- Migration `0007` (same file as above) also adds `collections(id, name, query, root, pinned, created_at)`.
- `api/routes_collections.py`: list, create, update, delete, plus `GET /collections/{id}/run` which runs `hybrid_search` for the saved query and root.
- Search page: a SAVE THIS SEARCH button. Sidebar: a COLLECTIONS group under the nav showing pinned collections (keep the row-height maths in `sidebar.js`: the nav indicator uses a fixed `ROW_HEIGHT`). Clicking one opens Search pre-filled and runs it.
- "Recent and relevant" on the Chat empty state: a new `GET /files/recent` that returns distinct files from `file_opens` (latest open per path, most recent first, limit 8) merged with files changed in the last 7 days (`sqlite_fts.list_files` mtime), filtered to files still in the index. Each row opens the file through the existing `/files/open` route, so open-history learning keeps working.
- The wipe button in "Learning from use" must also clear whatever this feature derives from opens. Collections are the user's own data and are not wiped by it.
- Tests for CRUD, run, recent ordering, missing-file filtering, and wipe behavior.

## Phase 6: appearance

Store `appearance = {theme: "system"|"light"|"dark", accent: "red"|"blue"|"green"|"violet", font_scale: 0.9..1.3, overlay_compact: bool, overlay_position: "cursor"|"center"|"top-right"}`.

- `style.css` already defines tokens on `:root` (`--paper`, `--ink`, `--red`, …). Add a `[data-theme="dark"]` token set and a `prefers-color-scheme` rule for `system`. Add accent tokens by driving the existing `--red*` variables or introducing `--accent*` and migrating usages. Check that nothing relies on a hard-coded colour.
- Apply on startup: `main.js` and `overlay.js` fetch `/personalize` after the backend is up and set `data-theme`, `data-accent` and a root `font-size`. Avoid a flash by applying the last known values synchronously from a tiny cached copy (this is a UI convenience only, so `localStorage` is fine here, wrapped in try/catch).
- Overlay: compact mode (smaller, fewer rows) and position. Position needs Electron: read how `placeOverlay()` in `ui/electron/main.js` works, then add an IPC call or have main fetch the preference over `backendPost`'s equivalent. Keep the preload bridge minimal. The existing overlay shortcut setting stays as it is.
- Settings page: an APPEARANCE section with live preview (changes apply immediately).
- A contrast test: compute WCAG ratios for each theme and accent pair for text and background tokens and fail below 4.5:1 for small text and 3:1 for large text and UI borders. Pick accent colours that pass, and adjust rather than skipping the test.

## Phase 7: performance profiles

Today these values come from `settings` (env or `.env`): `llm_idle_unload_s`, `caption_images`, `embed_keep_alive`, `min_free_ram_mb`, `history_turns`, `rerank_model`. The Settings page cannot change them.

- `personalize.effective(name)` returns the profile's override if set, else the `settings` value. Replace direct reads in `idle.py` (`_due`, `unload`), `ram.py`, `indexer`/`image_parser` (captions) and `routes_chat` with it. Keep `.env` working as the base.
- Profiles:
  - **battery**: 120 s idle unload, no captions, embedder `keep_alive` "30s", `history_turns` 3.
  - **balanced** (default): today's values.
  - **plugged in**: 1800 s unload, captions allowed, `history_turns` 6. It may suggest the 4B model **only** as an optional note when free RAM is high, and never switches the model by itself.
  - **auto**: picks battery or plugged in from `psutil.sensors_battery()`. It must degrade to balanced when `sensors_battery()` returns `None`.
- Expose a custom "unload the model after N minutes" field (0 = never) and a "stop indexing when free RAM is below N MB" field, validated to sane ranges.
- Settings page: a PERFORMANCE section showing the active profile, free RAM, and what each profile changes in plain words.
- Tests with `psutil` monkeypatched (on battery, plugged in, no battery) and with idle-unload timing using a fake clock like the existing code allows.

## Phase 8: assistant touches

Only what is genuinely missing. Voice, the briefing, and the nudge infrastructure exist.

- **Reminders tied to documents.** Migration `0007` adds `tasks.file_path TEXT`. `NewTask` and `TaskPatch` accept it (it must be an indexed file, validated server-side). A "REMIND ME" button on Search result rows and in the file card creates a task through the existing `/tasks/parse` → confirm flow, and the Tasks page and the notification show the file name with an OPEN action. `tasks._TASK_FIELDS` and the reminder event payload need the new field.
- **New-file nudge (opt-in).** `inbox_folders` (default empty). A new nudge kind in `briefing.due` such as `new_files` counts files that appeared in those folders since the last check, and respects the existing quiet hours, the daily cap and the once-per-day-per-kind log (`nudge_log`). The text offers to open the Organize page for that folder. It never moves anything. Settings: a folder picker list for inbox folders, off by default.
- The Settings page gets an "ABOUT ME" box (profile, Phase 1) at the top, since it is the most personal setting.

## Phase 9: finish

- Update `docs/ARCHITECTURE.md` (a new "Personalization" section: where preferences live, what is injected into prompts and why it is bounded, the memory review flow, folder profiles, performance profiles), `docs/ROADMAP.md`, `README.md`, and add the manual checks to `docs/TESTING-ON-WINDOWS.md`.
- Add the new modules and any hidden imports to `backend/lfa-backend.spec` if the PyInstaller build needs them. Run `scripts\build-backend.ps1` and its smoke test, and make sure the secret guard still passes.
- Run `pytest` in `backend\`, `python eval\assistant_eval.py`, and the UI build (`npm run build` in `ui\`). Report real results, not assumptions.
- End with a short summary listing what changed per phase, what you could not verify without a running Ollama or the Electron app on Windows, and anything you deliberately left out.

## Working rules

- Small, reviewable commits, one per phase, with clear messages.
- Don't rewrite working code for style. Match the existing conventions: type hints, the `ponytail:` comment style for known compromises, docstrings that explain why, and uppercase mono UI labels.
- If something here conflicts with what you find in the code, trust the code, say so, and adapt.
- Ask me before adding a dependency. Prefer the standard library, `psutil` (already used) and what is in `requirements.txt`.
- Do not touch `backend/build/`, `__pycache__`, eval result JSONs, or the `package-lock.json` outside `ui/`.
