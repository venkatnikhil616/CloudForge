import asyncio
import os
import socket
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

from sqlalchemy import select

from pkg.database import AsyncSessionLocal
from pkg.logger import get_logger, set_correlation_id
from pkg.messaging import get_rabbitmq_client
from pkg.models.attempt import TaskAttempt
from pkg.models.task import Task, TaskStatus
from pkg.redis_client import distributed_lock
from services.worker.tasks.registry import get_handler

logger = get_logger("worker-executor")
WORKER_ID = f"worker-{socket.gethostname()}-{os.getpid()}"


def calculate_exponential_backoff(attempt: int, base_seconds: int = 5) -> int:
    """Calculates backoff delay: base * (3 ** (attempt - 1)). Example: 5s, 15s, 45s, 135s."""
    return base_seconds * (3 ** max(attempt - 1, 0))


async def execute_task(task_payload: Dict[str, Any]) -> Tuple[bool, bool]:
    """
    Executes a task with distributed locking, idempotency check, timeout guard,
    attempt recording, and retry/DLQ state transitions.
   
    Returns: (is_success, should_requeue)
    """
    task_id = task_payload["id"]
    set_correlation_id(task_id)

    # 1. Acquire distributed lock via Redis to ensure only one worker processes this task
    async with distributed_lock(f"task:{task_id}", timeout_seconds=60) as lock_acquired:
        if not lock_acquired:
            logger.warning(f"Task {task_id} is currently locked by another worker. Requeuing.")
            return False, True

        start_time = time.time()
        attempt_number = 1

        async with AsyncSessionLocal() as db:
            # 2. Retrieve task state from PostgreSQL (System of Record)
            stmt = select(Task).where(Task.id == task_id)
            task = (await db.execute(stmt)).scalar_one_or_none()

            if not task:
                logger.error(f"Task {task_id} not found in database. Discarding.")
                return False, False

            # Check if task was cancelled or already succeeded
            if task.status == TaskStatus.CANCELLED:
                logger.info(f"Task {task_id} is CANCELLED. Skipping execution.")
                return True, False
            if task.status == TaskStatus.SUCCESS:
                logger.info(f"Task {task_id} already in SUCCESS state (Idempotent guard). Skipping.")
                return True, False

            task.current_attempt += 1
            attempt_number = task.current_attempt
            task.status = TaskStatus.RUNNING
            await db.commit()

            # Create attempt record
            attempt = TaskAttempt(
                id=str(uuid.uuid4()),
                task_id=task.id,
                attempt_number=attempt_number,
                status="RUNNING",
                worker_id=WORKER_ID,
                started_at=datetime.now(timezone.utc),
            )
            db.add(attempt)
            await db.commit()

            # 3. Execute with timeout
            handler = get_handler(task.task_type)
            success = False
            error_message = None
            stack_trace = None
            result = None

            try:
                result = await asyncio.wait_for(
                    handler(task.payload),
                    timeout=float(task.timeout_seconds)
                )
                success = True
            except asyncio.TimeoutError:
                error_message = f"Task exceeded timeout limit of {task.timeout_seconds} seconds"
                stack_trace = "TimeoutError"
            except Exception as e:
                error_message = str(e)
                stack_trace = traceback.format_exc()

            duration_ms = int((time.time() - start_time) * 1000)

            # 4. Handle Success or Failure
            attempt.finished_at = datetime.now(timezone.utc)
            attempt.duration_ms = duration_ms

            mq_client = await get_rabbitmq_client()

            if success:
                task.status = TaskStatus.SUCCESS
                task.result = result
                task.error_message = None
                attempt.status = "SUCCESS"
                await db.commit()

                # Publish task.completed notification event
                await mq_client.publish_event("notification.task.completed", {
                    "task_id": task.id,
                    "user_id": task.user_id,
                    "title": task.title,
                    "status": "SUCCESS",
                    "duration_ms": duration_ms,
                })
                logger.info(f"Task {task.id} succeeded in {duration_ms}ms")
                return True, False
            else:
                attempt.status = "FAILED"
                attempt.error_message = error_message
                attempt.stack_trace = stack_trace

                if task.current_attempt < task.max_retries:
                    task.status = TaskStatus.RETRY
                    task.error_message = error_message
                    await db.commit()

                    backoff_delay = calculate_exponential_backoff(task.current_attempt)
                    logger.warning(
                        f"Task {task.id} failed attempt {task.current_attempt}/{task.max_retries}. "
                        f"Retrying in {backoff_delay}s. Error: {error_message}"
                    )

                    # Sleep backoff then re-queue to RabbitMQ
                    await asyncio.sleep(min(backoff_delay, 15))  # bounded sleep for local testing
                    task.status = TaskStatus.QUEUED
                    await db.commit()
                    await mq_client.publish_task(task_payload, priority=task.priority, routing_key="task.retry")
                    return False, False
                else:
                    # Retry limit exceeded -> Route to Dead Letter Queue (DLQ)
                    task.status = TaskStatus.DEAD_LETTERED
                    task.error_message = f"Max retries ({task.max_retries}) exceeded. Last error: {error_message}"
                    await db.commit()

                    logger.error(f"Task {task.id} reached retry limit. Routed to Dead Letter Queue.")
                    await mq_client.publish_event("notification.task.failed", {
                        "task_id": task.id,
                        "user_id": task.user_id,
                        "title": task.title,
                        "status": "DEAD_LETTERED",
                        "error": task.error_message,
                    })
                    return False, False
