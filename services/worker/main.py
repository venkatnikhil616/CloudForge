import asyncio
import json
import signal
import sys
from pathlib import Path

# Ensure monorepo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from aio_pika.abc import AbstractIncomingMessage
from prometheus_client import Counter, Gauge, Histogram, start_http_server

from pkg.config import get_settings
from pkg.logger import get_logger
from pkg.messaging import get_rabbitmq_client
from services.worker.executor import WORKER_ID, execute_task

settings = get_settings()
logger = get_logger("worker")

# Worker Prometheus Metrics
TASKS_PROCESSED = Counter("worker_tasks_processed_total", "Total tasks processed", ["status", "task_type"])
TASKS_FAILED = Counter("worker_tasks_failed_total", "Total tasks failed", ["task_type"])
TASKS_RETRIED = Counter("worker_tasks_retried_total", "Total tasks retried", ["task_type"])
TASK_DURATION = Histogram("worker_task_duration_seconds", "Task execution duration in seconds", ["task_type"])
ACTIVE_WORKERS = Gauge("worker_active_count", "Current active worker instances")

running = True


def handle_shutdown(signum, frame):
    global running
    logger.info(f"Worker {WORKER_ID} received signal {signum}. Initiating graceful shutdown...")
    running = False


async def on_message(message: AbstractIncomingMessage) -> None:
    async with message.process(requeue=False, ignore_processed=True):
        try:
            payload = json.loads(message.body.decode("utf-8"))
            task_type = payload.get("task_type", "unknown")
            logger.info(f"Worker {WORKER_ID} received task {payload.get('id')} of type {task_type}")

            with TASK_DURATION.labels(task_type=task_type).time():
                success, should_requeue = await execute_task(payload)

            if success:
                TASKS_PROCESSED.labels(status="success", task_type=task_type).inc()
            elif should_requeue:
                TASKS_RETRIED.labels(task_type=task_type).inc()
                await message.nack(requeue=True)
                return
            else:
                TASKS_FAILED.labels(task_type=task_type).inc()
                TASKS_PROCESSED.labels(status="failed", task_type=task_type).inc()

        except Exception as e:
            logger.error(f"Unexpected error processing message: {e}")
            await message.nack(requeue=False)


async def poll_and_execute_tasks():
    from sqlalchemy import select

    from pkg.database import AsyncSessionLocal
    from pkg.models.task import Task, TaskStatus
    from pkg.redis_client import get_execution_mode

    # In manual mode, tasks wait in the queue until the user taps "Start Processing"
    mode = await get_execution_mode()
    if mode != "auto":
        return

    try:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(Task)
                .where(Task.status == TaskStatus.QUEUED)
                .order_by(Task.priority.desc(), Task.created_at.asc())
                .limit(settings.WORKER_CONCURRENCY)
            )
            tasks = (await session.execute(stmt)).scalars().all()
            for t in tasks:
                task_dict = {
                    "id": t.id,
                    "user_id": t.user_id,
                    "title": t.title,
                    "task_type": t.task_type,
                    "payload": t.payload or {},
                    "priority": t.priority,
                    "max_retries": t.max_retries,
                    "current_attempt": t.current_attempt,
                    "timeout_seconds": t.timeout_seconds,
                    "trace_id": t.trace_id,
                    "webhook_url": t.webhook_url,
                    "delay_seconds": t.delay_seconds,
                }
                asyncio.create_task(execute_task(task_dict))
    except Exception as e:
        logger.warning(f"Database polling check: {e}")


async def process_priority_queue() -> int:
    """
    Executes all currently QUEUED tasks strictly in Priority Order (P10 -> P1).
    Provides visual progress pacing between tasks for clear demo visibility.
    """
    from sqlalchemy import select
    from pkg.database import AsyncSessionLocal
    from pkg.models.task import Task, TaskStatus

    try:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(Task)
                .where(Task.status.in_([TaskStatus.QUEUED, TaskStatus.PENDING]))
                .order_by(Task.priority.desc(), Task.created_at.asc())
            )
            tasks = (await session.execute(stmt)).scalars().all()
            if not tasks:
                return 0

            # Promote any PENDING tasks to QUEUED
            for t in tasks:
                if t.status == TaskStatus.PENDING:
                    t.status = TaskStatus.QUEUED
            await session.commit()

            task_list = [
                {
                    "id": t.id,
                    "user_id": t.user_id,
                    "title": t.title,
                    "task_type": t.task_type,
                    "payload": t.payload or {},
                    "priority": t.priority,
                    "max_retries": t.max_retries,
                    "current_attempt": t.current_attempt,
                    "timeout_seconds": t.timeout_seconds,
                    "trace_id": t.trace_id,
                    "webhook_url": t.webhook_url,
                    "delay_seconds": t.delay_seconds,
                }
                for t in tasks
            ]

        logger.info(f"Starting manual priority dispatch for {len(task_list)} tasks (P10 -> P1)...")
        for task_dict in task_list:
            try:
                await execute_task(task_dict)
                # Pacing between tasks so UI updates reflect priority progression
                await asyncio.sleep(0.5)
            except Exception as ex:
                logger.error(f"Error processing priority task {task_dict['id']}: {ex}")

        return len(task_list)
    except Exception as e:
        logger.error(f"process_priority_queue error: {e}")
        return 0



async def run_worker():
    global running
    logger.info(f"Starting CloudTask Worker [{WORKER_ID}] with concurrency {settings.WORKER_CONCURRENCY}")
    ACTIVE_WORKERS.inc()

    mq_client = None
    try:
        mq_client = await get_rabbitmq_client()
        channel = mq_client.channel
        queue = await channel.get_queue(settings.RABBITMQ_TASK_QUEUE)
        await queue.consume(on_message)
        logger.info(f"Worker [{WORKER_ID}] subscribed to queue {settings.RABBITMQ_TASK_QUEUE}.")
    except Exception as e:
        logger.warning(f"RabbitMQ connection deferred ({e}). Operating in resilient database-backed polling mode.")

    while running:
        if mq_client is None:
            await poll_and_execute_tasks()
            await asyncio.sleep(2)
        else:
            await asyncio.sleep(1)

    logger.info(f"Worker [{WORKER_ID}] gracefully shutting down...")
    ACTIVE_WORKERS.dec()
    if mq_client:
        await mq_client.close()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Start Prometheus metrics HTTP server on port 9091
    try:
        start_http_server(9091)
        logger.info("Prometheus metrics server started on port 9091")
    except Exception as e:
        logger.warning(f"Could not bind metrics server: {e}")

    try:
        asyncio.run(run_worker())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Worker process exited cleanly.")
