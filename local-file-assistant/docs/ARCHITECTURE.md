# Architecture

Electron shell (ui/) around a FastAPI backend (backend/) that talks to Ollama on
localhost. One local model plays several roles through different prompts; everything
else is plain deterministic code.

## Processes

- **Electron main** (`ui/electron/main.js`) picks a free port, generates a random API
  token, starts the backend with both, and owns the main window, the Ctrl+Shift+Space
  overlay and the tray icon. Closing the window hides it; Quit is in the tray menu.
- **Backend** (`backend/run.py`) refuses to bind to anything but loopback. Every request
  needs a loopback Host header (blocks DNS rebinding), the bearer token and, from a
  browser, an allowed Origin (`app/security.py`). `/health` is public. No access log, so
  questions never land in log files. If the backend dies, Electron restarts it (3 tries)
  on the same port and token; on quit it kills the whole process tree.
- **Logs**: `%APPDATA%\LocalFileAssistant\logs\backend.log` (backend) and
  `%APPDATA%\Local File Assistant\logs\main.log` (Electron + backend console).
- **Pages** (`ui/index.html`, `ui/overlay.html`, built by Vite) get the port and token
  through the preload bridge (`window.lfa`), never from anywhere else.

## Indexing

`core/indexer.py` walks each indexed folder, hashes files (streamed), parses them into
chunks of ~250 words with 40 words of overlap (`core/chunking.py`), and stores them in
SQLite FTS5 (`db/sqlite_fts.py`) and LanceDB (`db/vector_store.py`), embedding in batches
of 32; the embedder sees each chunk with its file name and folder in front (`embed_text`), the
stored and cited text is the chunk alone. Each chunk keeps its location: page (PDF), slide
(pptx), sheet (xlsx), part (docx, txt/md/csv, html, eml, which have no reliable pages) or image. Unchanged files are skipped; files gone from disk
are pruned. If the embedder is down, files are still keyword-indexed and retried on the
next scan. Failures are logged and listed on the Index page with the reason.

Two rules keep the two stores honest. A file's content hash is written last (`mark_complete`),
after its vectors, so anything that fails or crashes part-way is retried by the next scan; a
blank hash means "keyword-only for now" and is counted per folder on the Index page. And a
file's chunks occupy one rowid range (`file id * CHUNKS_PER_FILE + chunk number`), because FTS5
reaches rowids through an index while `WHERE path = ?` reads the whole table.

Rescans skip files whose size and modified time are unchanged without reading them. A
scan can be stopped from the Index page (nothing is pruned on a partial walk). When free RAM
is below `MIN_FREE_RAM_MB` (default 1200), files are keyword-indexed only and get their
vectors on the next scan. Scanned PDF pages with no text layer are OCR'd when RapidOCR is
installed; images get OCR text and, optionally, a caption (docs/decisions/0001-images.md).

The SQLite schema is versioned. An older index is dropped on connect, its folders are
marked unscanned and rebuilt automatically at startup, and the LanceDB vectors are dropped
with it so the two stores never disagree.

`core/watcher.py` keeps the index current: create/modify re-index, delete/move remove the
old path, all debounced and run on one worker thread.

## Search and answers

- **Search** (`core/search/`): FTS5 with stopwords dropped and terms ORed (bm25 ranks),
  plus vector search, fused with Reciprocal Rank Fusion. Keyword search always works;
  semantic search is skipped if Ollama is slow or down.
- **Answerer** (`core/llm/answerer.py`) streams an answer from the top chunks, citing
  labels like `(report.pdf, page 2)`.
- **Verifier** (`core/llm/verifier.py`, no LLM call) checks each citation points at a
  retrieved location and that the claim's numbers and names appear in that source.

## Organizer

`core/llm/organizer.py` finds duplicates (same sha256) and backup/temp files itself, and
asks the model only which folder each remaining file belongs in. The plan is shown for
approval. `/organize/apply` validates every action first (inside an indexed folder, source
exists, destination free) and applies nothing if any is invalid. Moves are logged before
they happen (`undo_log.jsonl`) and can be undone per batch; deletes go to the Recycle Bin.

## Model backend

Ollama's OpenAI-compatible `/v1` API. `LLM_PROVIDER=openai` sends chat and embeddings to any
OpenAI-compatible server instead, e.g. OpenVINO Model Server on the NPU (docs/OPENVINO.md).
The model can be changed on the Settings page. The chat model is unloaded from Ollama after
`LLM_IDLE_UNLOAD_S` (default 600 s) without questions; the embedder after 2 minutes.

## Measuring quality

`backend/eval/run_eval.py` indexes a folder into a throwaway index, asks the questions in
`eval/questions.jsonl` (including ones the files can't answer) and reports answer accuracy,
retrieval hit rate, citation accuracy, verifier pass rate, latency and lowest free RAM.
