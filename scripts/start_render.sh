#!/usr/bin/env bash
set -e

echo "=== CloudTask Starting on Render ==="

export PYTHONPATH=/app:.
export PYTHONUNBUFFERED=1

# Start Unified CloudTask Engine on Render's $PORT
RENDER_PORT=${PORT:-8000}
echo "Starting CloudTask API Gateway on 0.0.0.0:$RENDER_PORT..."
exec python services/api-gateway/main.py
