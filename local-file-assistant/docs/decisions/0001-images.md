# 0001 — How images become searchable

Date: 2026-09-30. Status: accepted.

## Decision

Images are indexed from text only, from two optional sources:

1. **OCR** (RapidOCR, ONNX on CPU) — on when `rapidocr-onnxruntime` is installed. Also
   used for scanned PDF pages that have no text layer.
2. **Captions** from the local vision model (qwen3-vl) — off by default, turned on with
   `CAPTION_IMAGES=true` in `backend\.env`.

The stub `image_tagger.py` and the separate CLIP image-embedding plan are dropped.

## Why

- A caption or OCR text goes through the same keyword + vector index as documents, so
  images get citations, verification and hybrid ranking for free.
- CLIP would add a second embedding model (~600 MB RAM) and a second vector table. On a
  16 GB laptop that already holds the chat model and the text embedder, that costs more
  than it gives for a personal file collection, which is mostly documents and screenshots.
- The chat model is already a vision model, so captions need no extra download.
- Captioning costs several seconds per image on CPU, so it stays opt-in.

## Revisit when

Photo-heavy folders become a real use case (search by what a photo shows, with no text in
it). Then CLIP, or captions generated in the background after indexing, are the options.
