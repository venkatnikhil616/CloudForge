import asyncio
import json
import signal
import sys
from prometheus_client import Counter, Histogram, Gauge, start_http_server
from aio_pika.abc import AbstractIncomingMessage

from pkg.config import get_settings
from pkg.logger import get_logger
from pkg.messaging import get_rabbitmq_client
from services.worker.executor import execute_task, WORKER_ID

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


async def run_worker():
    global running
    logger.info(f"Starting CloudTask Worker [{WORKER_ID}] with concurrency {settings.WORKER_CONCURRENCY}")
    ACTIVE_WORKERS.inc()

    mq_client = await get_rabbitmq_client()
    channel = mq_client.channel
    queue = await channel.get_queue(settings.RABBITMQ_TASK_QUEUE)

    # Start consuming with prefetch limit
    await queue.consume(on_message)
    logger.info(f"Worker [{WORKER_ID}] subscribed to queue {settings.RABBITMQ_TASK_QUEUE}. Waiting for tasks...")

    while running:
        await asyncio.sleep(1)

    logger.info(f"Worker [{WORKER_ID}] gracefully shutting down...")
    ACTIVE_WORKERS.dec()
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
