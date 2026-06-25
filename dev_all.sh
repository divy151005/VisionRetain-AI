#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [ ! -x ".venv/bin/uvicorn" ]; then
  echo "Python environment is missing. Run ./start_all.sh once to install backend dependencies."
  exit 1
fi

.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8002 --workers 1 --env-file .env &
BACKEND_PID=$!

cleanup() {
  kill "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

npm run dev
