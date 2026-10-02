#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Starting CloudTask Engine (Local All-in-One Mode) ==="

# Prefer .venv python if available
if [ -f ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
elif command -v python3 &>/dev/null; then
  PYTHON_BIN="python3"
else
  PYTHON_BIN="python"
fi

export PYTHONPATH=.

echo "Using interpreter: $PYTHON_BIN"
echo "Initializing CloudTask Engine on http://localhost:8000 ..."
echo "--------------------------------------------------------"
echo "  Dashboard:          http://localhost:8000/dashboard"
echo "  API Portal:         http://localhost:8000/"
echo "  API Docs (Swagger): http://localhost:8000/docs"
echo "--------------------------------------------------------"

exec $PYTHON_BIN services/api-gateway/main.py
