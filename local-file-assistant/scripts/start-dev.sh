#!/usr/bin/env bash
# Runs the backend on its own (port 8756) for API work. The desktop app (cd ui && npm run dev)
# starts its own backend, so you don't need this for normal use. Linux twin of start-dev.ps1.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root/backend"
exec .venv/bin/python run.py
