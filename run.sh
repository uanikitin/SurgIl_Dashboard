#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "❌ .venv не найден в $(pwd)"
  exit 1
fi

source .venv/bin/activate

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

echo "→ venv: $(which python)"
echo "→ http://localhost:${PORT}"
echo

exec uvicorn backend.app:app --reload --host "$HOST" --port "$PORT"
