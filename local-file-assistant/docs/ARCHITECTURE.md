# Architecture

One local model, several roles via prompts — not multiple models. Everything except
the LLM roles is plain deterministic code.

- **Answerer** — writes a cited answer from the top ~5 retrieved chunks.
- **Verifier** — checks each citation's text actually exists in the cited file/page.
- **Organizer** — proposes a file reorganization as a JSON plan only. The UI shows the
  plan; the user approves or rejects; only then does `file_ops` apply it (reversibly).
- **Image tagger** — background, low priority; runs after CLIP-based image search works.

Model backend: Ollama's OpenAI-compatible `/v1` API at `localhost:11434`, so the
backend can swap to OpenVINO Model Server later by changing one base URL
(`backend/app/core/llm/client.py`).

Search is hybrid: SQLite FTS5 (keyword, instant) + a vector store (semantic), ranked
together. Search never waits on the LLM — the AI answer streams separately.

Indexing is incremental: file hashes are stored, unchanged files are skipped on rescan.
