#!/usr/bin/env bash
set -e

echo "=== CloudTask Local Service Runner ==="

# Check for .env file
if [ ! -f ".env" ]; then
    echo "Creating .env from .env.example..."
    cp .env.example .env
fi

# Check Python virtual environment
if [ ! -d ".venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv .venv
fi

echo "Activating virtual environment..."
source .venv/bin/activate

echo "Installing dependencies..."
pip install -q -r requirements.txt

echo "Running initial database migration & seed..."
python database/seeds/seed_data.py || echo "Warning: Database not reachable yet. Ensure PostgreSQL is running on port 5432."

echo ""
echo "Starting CloudTask microservices in background..."

python services/auth-service/main.py &
AUTH_PID=$!
echo "-> Auth Service running (PID $AUTH_PID) on port 8001"

python services/task-service/main.py &
TASK_PID=$!
echo "-> Task Service running (PID $TASK_PID) on port 8002"

python services/worker/main.py &
WORKER_PID=$!
echo "-> Worker Service running (PID $WORKER_PID)"

python services/scheduler/main.py &
SCHED_PID=$!
echo "-> Scheduler Service running (PID $SCHED_PID)"

python services/notification-service/main.py &
NOTIF_PID=$!
echo "-> Notification Service running (PID $NOTIF_PID)"

python services/api-gateway/main.py &
GATEWAY_PID=$!
echo "-> API Gateway running (PID $GATEWAY_PID) on port 8000"

echo ""
echo "All services launched! Press Ctrl+C to stop all."

trap "kill $AUTH_PID $TASK_PID $WORKER_PID $SCHED_PID $NOTIF_PID $GATEWAY_PID 2>/dev/null; exit 0" INT TERM
wait
