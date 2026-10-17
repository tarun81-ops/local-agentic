#!/usr/bin/env bash
# Freezes the Python backend into backend/dist/lfa-backend/lfa-backend with PyInstaller, checks that
# no secrets were bundled, then starts it once to prove it runs. Linux twin of build-backend.ps1;
# electron-builder then ships that folder inside the AppImage/.deb (ui/package.json extraResources).
# PyInstaller can't cross-compile: run this on Linux for the Linux build, the .ps1 on Windows.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root/backend"
py=.venv/bin/python

extra=()
# OCR is optional; bundle its models only when it's installed.
if "$py" -c "import rapidocr_onnxruntime" 2>/dev/null; then extra+=(--collect-all rapidocr_onnxruntime); fi

.venv/bin/pyinstaller run.py \
  --name lfa-backend \
  --noconfirm --clean --onedir --console \
  --collect-all onnxruntime \
  --collect-all tokenizers \
  --collect-all lancedb \
  --collect-all pyarrow \
  --collect-all pymupdf \
  --add-data "app/db/migrations:app/db/migrations" \
  --collect-all dateparser \
  --collect-all tzlocal \
  --collect-all tzdata \
  --collect-all faster_whisper \
  --collect-all ctranslate2 \
  --collect-all piper \
  --collect-all av \
  --collect-submodules uvicorn \
  --collect-submodules app \
  --collect-submodules send2trash \
  --collect-submodules watchdog \
  "${extra[@]}"

dist="$root/backend/dist/lfa-backend"
exe="$dist/lfa-backend"

# The search re-ranker ships next to the binary, so the installed app never downloads anything.
model="$("$py" -c "from app.config import settings; from app.core.search import reranker; print(reranker.model_dir(settings.rerank_model) if reranker.available() else '')")"
if [ -n "$model" ]; then
  mkdir -p "$dist/models"; cp -r "$model" "$dist/models/"
else
  echo "WARNING: no re-ranker model found; run scripts/setup.sh first to include it."
fi

# 1. Secrets must never ship: .env holds the local API token and settings.
leaked="$(find "$dist" \( -name .env -o -name api_token.txt -o -name '*.db' \) -print)"
if [ -n "$leaked" ]; then echo "Refusing to ship secrets/data: $leaked" >&2; exit 1; fi

# 2. Smoke test: the frozen backend starts, answers /health, and rejects a missing token.
port=18756
tmp="$(mktemp -d)"
export PORT=$port API_TOKEN=smoke-token DATA_DIR="$tmp" DB_PATH="$tmp/index.db" VECTOR_DB_DIR="$tmp/lancedb" \
       ASSISTANT_DB_PATH="$tmp/assistant.db" VOICE_MODELS_DIR="$tmp/models"
(cd "$dist" && "$exe") >/dev/null 2>&1 &
pid=$!
cleanup() { kill "$pid" 2>/dev/null || true; rm -rf "$tmp"; }
trap cleanup EXIT
ok=0
for _ in $(seq 60); do
  sleep 0.5
  if curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1; then ok=1; break; fi
done
[ "$ok" = 1 ] || { echo "Frozen backend did not answer /health within 30 s" >&2; exit 1; }
auth=(-H "Authorization: Bearer smoke-token")
curl -fsS "${auth[@]}" "http://127.0.0.1:$port/settings" >/dev/null
for route in conversations memory tasks personalize collections; do
  curl -fsS "${auth[@]}" "http://127.0.0.1:$port/$route" >/dev/null || { echo "/$route failed in the frozen backend" >&2; exit 1; }
done
code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/settings")"
[ "$code" = 401 ] || { echo "API answered without a token (HTTP $code)" >&2; exit 1; }
echo "Smoke test OK."
echo "Built backend/dist/lfa-backend. Now: cd ui && npm run build:linux"
