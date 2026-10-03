"""Re-ranks search candidates with a cross-encoder: a small model that reads the question and
each passage together, where the keyword and vector searches scored them separately. Runs on
the CPU with ONNX Runtime; no torch.

A model is a folder under settings.models_dir holding model.onnx and tokenizer.json. It is
fetched once (setup does this), never at search time, so searching stays offline:

    .venv\\Scripts\\python.exe -m app.core.search.reranker Xenova/ms-marco-MiniLM-L-6-v2
"""
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np

from app.config import settings

MAX_TOKENS = 512
# int8-quantized export: about a quarter of the fp32 size and faster on a CPU.
REMOTE_FILES = {"model.onnx": "onnx/model_quantized.onnx", "tokenizer.json": "tokenizer.json"}


def model_dir(name: str) -> Path:
    return settings.models_dir / name.replace("/", "--")


def available() -> bool:
    d = model_dir(settings.rerank_model) if settings.rerank_model else None
    return d is not None and all((d / f).is_file() for f in REMOTE_FILES)


@lru_cache(maxsize=2)
def _load(name: str):
    import onnxruntime as ort
    from tokenizers import Tokenizer

    d = model_dir(name)
    tok = Tokenizer.from_file(str(d / "tokenizer.json"))
    tok.enable_truncation(MAX_TOKENS)  # pairs: the longer side (the passage) is cut first
    tok.no_padding()
    pad_id = next((i for i in map(tok.token_to_id, ("[PAD]", "<pad>")) if i is not None), 0)
    session = ort.InferenceSession(str(d / "model.onnx"), providers=["CPUExecutionProvider"])
    return tok, session, {i.name for i in session.get_inputs()}, pad_id


def scores(query: str, passages: list[str], name: str | None = None) -> list[float]:
    """One relevance score per passage, higher is better (raw logits: compare, don't threshold)."""
    tok, session, inputs, pad_id = _load(name or settings.rerank_model)
    enc = tok.encode_batch([(query, p) for p in passages])
    width = max(len(e.ids) for e in enc)

    def pad(rows, value):
        return np.array([r + [value] * (width - len(r)) for r in rows], dtype=np.int64)

    feed = {
        "input_ids": pad([e.ids for e in enc], pad_id),
        "attention_mask": pad([e.attention_mask for e in enc], 0),
        "token_type_ids": pad([e.type_ids for e in enc], 0),
    }
    logits = session.run(None, {k: v for k, v in feed.items() if k in inputs})[0]
    return logits[:, -1].tolist()  # a single relevance logit, or the "relevant" class of two


def passage(row: dict) -> str:
    """What the cross-encoder reads: the file and folder name too, as the embedder does, so a
    file found by its name ("lease agreement") isn't demoted for not repeating it in the text."""
    p = Path(row["path"])
    return f"File: {p.name}\nFolder: {p.parent.name}\n\n{row['text']}"


def rerank(query: str, rows: list[dict]) -> list[dict]:
    """rows best-first; returns the same rows ordered by the cross-encoder (ties keep their order)."""
    s = scores(query, [passage(r) for r in rows])
    return [rows[i] for i in sorted(range(len(rows)), key=s.__getitem__, reverse=True)]


def download(repo: str) -> Path:
    """Fetches a Hugging Face ONNX export into models_dir. Setup-time only."""
    import httpx

    d = model_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    if all((d / f).is_file() for f in REMOTE_FILES):
        return d  # already here; delete the folder to fetch it again
    for local, remote in REMOTE_FILES.items():
        part = d / f"{local}.part"
        with httpx.stream("GET", f"https://huggingface.co/{repo}/resolve/main/{remote}", follow_redirects=True, timeout=60) as r:
            r.raise_for_status()
            with open(part, "wb") as f:
                for block in r.iter_bytes(1 << 20):
                    f.write(block)
        part.replace(d / local)  # a half-finished download never looks like a model
    return d


if __name__ == "__main__":
    repos = sys.argv[1:] or [m for m in [settings.rerank_model] if m]
    if not repos:
        print("RERANK_MODEL is empty: re-ranking is off, nothing to download.")
    for repo in repos:
        print("ready:", download(repo))
