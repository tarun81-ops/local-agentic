# Roadmap

Done
- Vertical slice, hybrid search, verifier, organizer with reversible apply.
- Electron app with the Modernist UI: Ask, Search, Organize, Index, Settings, and the
  Ctrl+Shift+Space overlay.
- Parsers for PDF, docx, pptx, xlsx; OCR for images when rapidocr is installed.
- Security: token always required, Origin check, paths confined to indexed folders.
- Packaging: PyInstaller backend inside the NSIS installer, free port, tray.

- Hardening pass (2026-09-30): schema upgrade rebuilds, RAM guard + idle unload, fast
  rescans + stop, scanned-PDF OCR, optional image captions, organizer checks for Windows
  names/long paths/links/case, Host-header check, logs, backend auto-restart, CI, eval harness.

Next
1. Run docs/TESTING-ON-WINDOWS.md on the laptop and record an eval baseline for qwen3-vl:2b.
2. Grow eval/questions.jsonl with 30–50 questions over real documents; tune chunk size and
   the verifier threshold against it. The verifier checks a claim is in the source, not
   that it answers the question: an eval run with a stand-in model showed on-topic
   citations for off-topic answers, so answer relevance needs its own check.
3. Multi-turn Ask (send earlier turns as context).
4. Benchmark OpenVINO Model Server on the NPU against Ollama on CPU (docs/OPENVINO.md).
