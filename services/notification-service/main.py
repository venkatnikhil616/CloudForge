import asyncio
import hashlib
import hmac
import json
import signal
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Ensure monorepo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import httpx
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
    """Processes task event, dispatches mock email, and triggers HMAC-signed webhook callbacks."""
    task_id = data.get("task_id")
    user_id = data.get("user_id", "system")
    title = data.get("title", "Task")
    status = data.get("status", "UNKNOWN")
    webhook_url = data.get("webhook_url")

    if status == "SUCCESS":
        subject = f"Task Completed: {title}"
        body = f"Your task '{title}' (ID: {task_id}) finished successfully in {data.get('duration_ms', 0)}ms."
    elif status == "DEAD_LETTERED":
        subject = f"Task Failed (DLQ): {title}"
        body = f"Your task '{title}' (ID: {task_id}) permanently failed: {data.get('error', 'Unknown error')}."
    else:
        subject = f"CloudTask Update: {title}"
        body = f"Task '{title}' (ID: {task_id}) changed state to {status}."

    logger.info(f"[DISPATCH EMAIL] Subject: {subject} | Recipient: user-{user_id}@cloudtask.dev")

    # 1. Persist mock email log
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

    # 2. Outgoing Webhook Callback with HMAC-SHA256 Signature (Stripe/GitHub style)
    if webhook_url:
        webhook_payload = {
            "event": event_type,
            "task_id": task_id,
            "user_id": user_id,
            "title": title,
            "status": status,
            "duration_ms": data.get("duration_ms"),
            "trace_id": data.get("trace_id"),
            "result": data.get("result"),
            "error": data.get("error"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        timestamp_epoch = int(time.time())
        payload_bytes = json.dumps(webhook_payload, sort_keys=True).encode("utf-8")
        secret = settings.JWT_SECRET_KEY
        to_sign = f"{timestamp_epoch}.".encode("utf-8") + payload_bytes
        signature = hmac.new(secret.encode("utf-8"), to_sign, hashlib.sha256).hexdigest()

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "CloudTask-Webhook-Dispatcher/1.0",
            "X-CloudTask-Event": event_type,
            "X-CloudTask-Delivery": str(uuid.uuid4()),
            "X-CloudTask-Timestamp": str(timestamp_epoch),
            "X-CloudTask-Signature": f"t={timestamp_epoch},v1={signature}",
        }

        webhook_status = "DELIVERED"
        webhook_msg = "HTTP 200 OK"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(webhook_url, content=payload_bytes, headers=headers)
                if resp.status_code >= 400:
                    webhook_status = "FAILED"
                    webhook_msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
                else:
                    webhook_msg = f"HTTP {resp.status_code}"
        except Exception as exc:
            webhook_status = "FAILED"
            webhook_msg = f"Delivery connection error: {exc}"

        logger.info(f"[DISPATCH WEBHOOK] Target: {webhook_url} | Status: {webhook_status} ({webhook_msg})")

        async with AsyncSessionLocal() as db:
            wh_log = NotificationLog(
                id=str(uuid.uuid4()),
                user_id=str(user_id),
                task_id=task_id,
                event_type=event_type,
                channel="webhook",
                recipient=webhook_url,
                status=webhook_status,
                message=f"Webhook delivery to {webhook_url} ({webhook_status}): {webhook_msg}",
                created_at=datetime.now(timezone.utc),
            )
            db.add(wh_log)
            await db.commit()

        NOTIFICATIONS_SENT.labels(channel="webhook", status=webhook_status).inc()


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
