import asyncio
import json
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
from pkg.redis_client import distributed_lock, get_redis_client
from services.worker.tasks.registry import get_handler

logger = get_logger("worker-executor")
WORKER_ID = f"worker-{socket.gethostname()}-{os.getpid()}"


def calculate_exponential_backoff(attempt: int, base_seconds: int = 5) -> int:
    """Calculates backoff delay: base * (3 ** (attempt - 1)). Example: 5s, 15s, 45s, 135s."""
    return base_seconds * (3 ** max(attempt - 1, 0))


async def publish_progress(task_id: str, progress: int, status: str, message: str = "") -> None:
    """Emits real-time progress update to Redis pub/sub channel for SSE/WebSockets and updates DB."""
    try:
        redis = get_redis_client()
        payload = json.dumps({
            "task_id": task_id,
            "progress": progress,
            "status": status,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        await redis.publish(f"task:progress:{task_id}", payload)
    except Exception as e:
        logger.warning(f"Failed to publish progress for task {task_id}: {e}")


async def check_and_trigger_dependents(completed_task_id: str) -> None:
    """DAG Resolver: releases dependent tasks once their prerequisites are completed."""
    try:
        async with AsyncSessionLocal() as db:
            stmt = select(Task).where(Task.status == TaskStatus.PENDING)
            pending_tasks = (await db.execute(stmt)).scalars().all()

            mq_client = None
            for p_task in pending_tasks:
                if completed_task_id in p_task.depends_on:
                    dep_stmt = select(Task).where(Task.id.in_(p_task.depends_on))
                    dep_tasks = (await db.execute(dep_stmt)).scalars().all()

                    all_succeeded = all(d.status == TaskStatus.SUCCESS for d in dep_tasks) and len(dep_tasks) == len(p_task.depends_on)
                    if all_succeeded:
                        p_task.status = TaskStatus.QUEUED
                        await db.commit()

                        try:
                            if not mq_client:
                                mq_client = await get_rabbitmq_client()

                            task_message = {
                                "id": p_task.id,
                                "user_id": p_task.user_id,
                                "title": p_task.title,
                                "task_type": p_task.task_type,
                                "payload": p_task.payload,
                                "priority": p_task.priority,
                                "max_retries": p_task.max_retries,
                                "current_attempt": p_task.current_attempt,
                                "timeout_seconds": p_task.timeout_seconds,
                                "trace_id": p_task.trace_id,
                            }
                            await mq_client.publish_task(
                                task_payload=task_message,
                                priority=p_task.priority,
                                routing_key="task.created",
                            )
                        except Exception as mq_err:
                            logger.warning(f"RabbitMQ publish for DAG task {p_task.id} deferred: {mq_err}")
                        logger.info(f"DAG: Task {p_task.id} prerequisites satisfied! Queued for execution.")
    except Exception as e:
        logger.error(f"Error checking DAG dependents for {completed_task_id}: {e}")


async def execute_task(task_payload: Dict[str, Any]) -> Tuple[bool, bool]:
    """
    Executes task with distributed locking, idempotency guard, timeout protection,
    real-time progress updates, preemption cancellation listener, and DAG resolution.
    """
    task_id = task_payload["id"]
    trace_id = task_payload.get("trace_id") or f"trace-{uuid.uuid4().hex[:16]}"
    set_correlation_id(task_id)

    # 1. Acquire distributed lock via Redis
    async with distributed_lock(f"task:{task_id}", timeout_seconds=60) as lock_acquired:
        if not lock_acquired:
            logger.warning(f"Task {task_id} is currently locked by another worker. Requeuing.")
            return False, True

        start_time = time.time()

        async with AsyncSessionLocal() as db:
            stmt = select(Task).where(Task.id == task_id)
            task = (await db.execute(stmt)).scalar_one_or_none()

            if not task:
                logger.error(f"Task {task_id} not found in database. Discarding.")
                return False, False

            if task.status == TaskStatus.CANCELLED:
                logger.info(f"Task {task_id} is CANCELLED. Skipping execution.")
                return True, False
            if task.status == TaskStatus.SUCCESS:
                logger.info(f"Task {task_id} already in SUCCESS state (Idempotent guard). Skipping.")
                return True, False

            task.current_attempt += 1
            attempt_number = task.current_attempt
            task.status = TaskStatus.RUNNING
            task.progress = 25
            task.trace_id = trace_id
            await db.commit()

            attempt = TaskAttempt(
                id=str(uuid.uuid4()),
                task_id=task.id,
                attempt_number=attempt_number,
                status="RUNNING",
                worker_id=WORKER_ID,
                trace_id=trace_id,
                started_at=datetime.now(timezone.utc),
            )
            db.add(attempt)
            await db.commit()

            await publish_progress(task_id, 25, "RUNNING", "Worker picked task, starting execution...")
            # Pacing to guarantee visual presence in RUNNING column for live dashboard
            await asyncio.sleep(0.4)

            # 2. Worker Preemption: setup abort listener
            abort_pubsub = None
            watcher_task = None
            try:
                redis = get_redis_client()
                abort_pubsub = redis.pubsub()
                await abort_pubsub.subscribe(f"task:abort:{task_id}")

                async def abort_watcher():
                    nonlocal aborted
                    while True:
                        msg = await abort_pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
                        if msg:
                            aborted = True
                            break
                        await asyncio.sleep(0.2)

                watcher_task = asyncio.create_task(abort_watcher())
            except Exception as e:
                logger.warning(f"Redis abort watcher unavailable: {e}")

            handler = get_handler(task.task_type)
            success = False
            aborted = False
            error_message = None
            stack_trace = None
            result = None

            try:
                # Run handler and progress updates concurrently
                execution_coro = handler(task.payload)
                wait_tasks = [asyncio.create_task(execution_coro)]
                if watcher_task:
                    wait_tasks.append(watcher_task)

                done, pending = await asyncio.wait(
                    wait_tasks,
                    timeout=float(task.timeout_seconds),
                    return_when=asyncio.FIRST_COMPLETED
                )

                if aborted:
                    error_message = "Task preemption: Execution aborted by user cancellation signal"
                else:
                    for finished in done:
                        if finished != watcher_task:
                            result = finished.result()
                            success = True

                # Progress 70%
                task.progress = 70
                await db.commit()
                await publish_progress(task_id, 70, "RUNNING", "Computation complete, persisting results...")
                await asyncio.sleep(0.3)

            except asyncio.TimeoutError:
                error_message = f"Task exceeded timeout limit of {task.timeout_seconds} seconds"
                stack_trace = "TimeoutError"
            except Exception as e:
                error_message = str(e)
                stack_trace = traceback.format_exc()
            finally:
                if watcher_task:
                    watcher_task.cancel()
                if abort_pubsub:
                    try:
                        await abort_pubsub.unsubscribe(f"task:abort:{task_id}")
                    except Exception:
                        pass

            duration_ms = int((time.time() - start_time) * 1000)
            attempt.finished_at = datetime.now(timezone.utc)
            attempt.duration_ms = duration_ms

            mq_client = None
            try:
                mq_client = await get_rabbitmq_client()
            except Exception as e:
                logger.warning(f"RabbitMQ client deferred/unavailable: {e}")

            if aborted:
                task.status = TaskStatus.CANCELLED
                task.progress = 0
                attempt.status = "CANCELLED"
                attempt.error_message = error_message
                await db.commit()
                await publish_progress(task_id, 0, "CANCELLED", error_message)
                logger.warning(f"Task {task.id} aborted cleanly via worker preemption.")
                return True, False

            if success:
                task.status = TaskStatus.SUCCESS
                task.progress = 100
                task.result = result
                task.error_message = None
                attempt.status = "SUCCESS"
                await db.commit()

                await publish_progress(task_id, 100, "SUCCESS", "Task execution finished successfully")

                # Trigger DAG resolution for dependent tasks
                asyncio.create_task(check_and_trigger_dependents(task.id))

                if mq_client:
                    try:
                        await mq_client.publish_event("notification.task.completed", {
                            "task_id": task.id,
                            "user_id": task.user_id,
                            "title": task.title,
                            "status": "SUCCESS",
                            "duration_ms": duration_ms,
                            "trace_id": trace_id,
                            "webhook_url": task.webhook_url,
                            "result": task.result,
                        })
                    except Exception as e:
                        logger.warning(f"Failed to publish task completion event: {e}")
                logger.info(f"Task {task.id} succeeded in {duration_ms}ms (Trace: {trace_id})")
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
                    await publish_progress(task_id, 0, "RETRY", f"Attempt {task.current_attempt} failed. Retrying in {backoff_delay}s...")
                    logger.warning(
                        f"Task {task.id} failed attempt {task.current_attempt}/{task.max_retries}. "
                        f"Retrying in {backoff_delay}s."
                    )

                    await asyncio.sleep(min(backoff_delay, 15))
                    task.status = TaskStatus.QUEUED
                    await db.commit()
                    if mq_client:
                        try:
                            await mq_client.publish_task(task_payload, priority=task.priority, routing_key="task.retry")
                        except Exception as e:
                            logger.warning(f"Failed to publish retry task to queue: {e}")
                    return False, False
                else:
                    task.status = TaskStatus.DEAD_LETTERED
                    task.error_message = f"Max retries ({task.max_retries}) exceeded. Last error: {error_message}"
                    task.progress = 0
                    await db.commit()

                    await publish_progress(task_id, 0, "DEAD_LETTERED", task.error_message)
                    logger.error(f"Task {task.id} routed to Dead Letter Queue.")
                    if mq_client:
                        try:
                            await mq_client.publish_event("notification.task.failed", {
                                "task_id": task.id,
                                "user_id": task.user_id,
                                "title": task.title,
                                "status": "DEAD_LETTERED",
                                "error": task.error_message,
                                "trace_id": trace_id,
                                "webhook_url": task.webhook_url,
                            })
                        except Exception as e:
                            logger.warning(f"Failed to publish failure notification: {e}")
                    return False, False
