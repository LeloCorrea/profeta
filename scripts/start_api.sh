#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"

cd "$ROOT_DIR"

mkdir -p "$ROOT_DIR/logs" "$ROOT_DIR/data" "$ROOT_DIR/data/audio"

source "$VENV_DIR/bin/activate"

exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
