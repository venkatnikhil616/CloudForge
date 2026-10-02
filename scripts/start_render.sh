#!/usr/bin/env bash
set -e

echo "=== CloudTask Starting on Render ==="

export PYTHONPATH=/app:.

# 1. Automatic Database Seeding / Migration (runs concurrently)
echo "Running database initialization in background..."
python database/seeds/seed_data.py &

# 2. Start internal backend microservices in background
echo "Starting Auth Service on internal port 8001..."
python services/auth-service/main.py &
AUTH_PID=$!

echo "Starting Task Service on internal port 8002..."
python services/task-service/main.py &
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
exec python services/api-gateway/main.py
