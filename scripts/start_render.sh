#!/usr/bin/env bash
set -e

echo "=== CloudTask Starting on Render ==="

# 1. Automatic Database Seeding / Migration
echo "Running database initialization and seeds..."
python database/seeds/seed_data.py || echo "Database seed skipped or already initialized."

# 2. Start internal backend microservices in background
echo "Starting Auth Service on internal port 8001..."
python -c "import uvicorn; uvicorn.run('services.auth-service.main:app', host='127.0.0.1', port=8001)" &
AUTH_PID=$!

echo "Starting Task Service on internal port 8002..."
python -c "import uvicorn; uvicorn.run('services.task-service.main:app', host='127.0.0.1', port=8002)" &
TASK_PID=$!

echo "Starting Distributed Worker..."
python services/worker/main.py &
WORKER_PID=$!

echo "Starting Scheduler Service..."
python services/scheduler/main.py &
SCHED_PID=$!

echo "Starting Notification Service..."
python services/notification-service/main.py &
NOTIF_PID=$!

# Trap signals to shut down child processes cleanly
trap "kill $AUTH_PID $TASK_PID $WORKER_PID $SCHED_PID $NOTIF_PID 2>/dev/null; exit 0" INT TERM

# 3. Start API Gateway in foreground on Render's $PORT
RENDER_PORT=${PORT:-8000}
echo "Starting CloudTask API Gateway on 0.0.0.0:$RENDER_PORT..."
exec python -c "import uvicorn; uvicorn.run('services.api-gateway.main:app', host='0.0.0.0', port=$RENDER_PORT)"
