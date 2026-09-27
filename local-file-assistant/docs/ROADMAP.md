# Roadmap

1. **Vertical slice (current)** — index a test folder (PDF/docx) → FTS5 keyword search →
   one working Ollama call that answers with a citation. Measure RAM/latency before going further.
2. Hybrid search — add vector store (LanceDB/Chroma) + embeddings, rank alongside FTS5.
3. Verifier role — confirm citations before showing an answer.
4. Organizer role — propose JSON reorg plans; apply only after user approval.
5. Image support — CLIP search first, background captioning later.
6. Electron shell around the working backend.
