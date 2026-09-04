import asyncio
import json
import signal
import uuid
from datetime import datetime, timezone

from aio_pika.abc import AbstractIncomingMessage
from prometheus_client import Counter, start_http_server

from pkg.config import get_settings
from pkg.database import AsyncSessionLocal
from pkg.logger import get_logger
from pkg.messaging import get_rabbitmq_client
from pkg.models.notification import NotificationLog

settings = get_settings()
logger = get_logger("notification-service")

NOTIFICATIONS_SENT = Counter("notifications_sent_total", "Total notifications dispatched", ["channel", "status"])
running = True


def handle_shutdown(signum, frame):
    global running
    logger.info(f"Notification service received signal {signum}. Stopping...")
    running = False


async def handle_notification(event_type: str, data: dict):
    """Processes task event and dispatches mock email/system notification."""
    task_id = data.get("task_id")
    user_id = data.get("user_id", "system")
    title = data.get("title", "Task")
    status = data.get("status", "UNKNOWN")

    if status == "SUCCESS":
        subject = f"✅ Task Completed: {title}"
        body = f"Your task '{title}' (ID: {task_id}) finished successfully in {data.get('duration_ms', 0)}ms."
    elif status == "DEAD_LETTERED":
        subject = f"🚨 Task Failed (DLQ): {title}"
        body = f"Your task '{title}' (ID: {task_id}) permanently failed: {data.get('error', 'Unknown error')}."
    else:
        subject = f"ℹ️ CloudTask Update: {title}"
        body = f"Task '{title}' (ID: {task_id}) changed state to {status}."

    logger.info(f"[DISPATCH NOTIFICATION] Subject: {subject} | Recipient: user-{user_id}@cloudtask.dev")

    # Persist log to PostgreSQL
    async with AsyncSessionLocal() as db:
        record = NotificationLog(
            id=str(uuid.uuid4()),
            user_id=str(user_id),
            task_id=task_id,
            event_type=event_type,
            channel="email",
            recipient=f"user-{user_id}@cloudtask.dev",
            status="SENT",
            message=body,
            created_at=datetime.now(timezone.utc),
        )
        db.add(record)
        await db.commit()

    NOTIFICATIONS_SENT.labels(channel="email", status="SENT").inc()


async def on_notification_message(message: AbstractIncomingMessage):
    async with message.process(requeue=False):
        try:
            payload = json.loads(message.body.decode("utf-8"))
            routing_key = message.routing_key or "notification.general"
            logger.info(f"Received notification event: {routing_key}")
            await handle_notification(routing_key, payload)
        except Exception as e:
            logger.error(f"Error handling notification: {e}")
            NOTIFICATIONS_SENT.labels(channel="email", status="FAILED").inc()


async def run_notification_service():
    global running
    logger.info("Starting CloudTask Notification Service...")

    mq_client = await get_rabbitmq_client()
    channel = mq_client.channel
    queue = await channel.get_queue(settings.RABBITMQ_NOTIFICATION_QUEUE)

    await queue.consume(on_notification_message)
    logger.info(f"Subscribed to queue {settings.RABBITMQ_NOTIFICATION_QUEUE}")

    while running:
        await asyncio.sleep(1)

    logger.info("Notification service shutting down...")
    await mq_client.close()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    try:
        start_http_server(9093)
        logger.info("Notification metrics server started on port 9093")
    except Exception as e:
        logger.warning(f"Could not bind metrics server: {e}")

    try:
        asyncio.run(run_notification_service())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Notification service process stopped.")
