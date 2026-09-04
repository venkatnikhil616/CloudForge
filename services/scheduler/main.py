import asyncio
import signal
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from croniter import croniter
from prometheus_client import Counter, Gauge, start_http_server
from sqlalchemy import select

from pkg.config import get_settings
from pkg.database import AsyncSessionLocal
from pkg.logger import get_logger
from pkg.messaging import get_rabbitmq_client
from pkg.models.schedule import TaskSchedule
from pkg.models.task import Task, TaskStatus
from pkg.redis_client import distributed_lock

settings = get_settings()
logger = get_logger("scheduler")

SCHEDULED_TASKS_FIRED = Counter("scheduler_tasks_fired_total", "Total scheduled tasks published to queue")
SCHEDULER_ERRORS = Counter("scheduler_errors_total", "Total scheduler execution errors")
SCHEDULER_LEADER_STATUS = Gauge("scheduler_is_leader", "1 if this replica is the active leader, 0 otherwise")

running = True


def handle_shutdown(signum, frame):
    global running
    logger.info(f"Scheduler received signal {signum}. Stopping...")
    running = False


def calculate_next_run(schedule: TaskSchedule) -> Optional[datetime]:
    """Calculates next run time using cron expression or interval seconds."""
    now = datetime.now(timezone.utc)
    if schedule.cron_expression:
        try:
            itr = croniter(schedule.cron_expression, now)
            return itr.get_next(datetime)
        except Exception as e:
            logger.error(f"Failed parsing cron expression '{schedule.cron_expression}': {e}")
            return None
    elif schedule.interval_seconds:
        return now + timedelta(seconds=schedule.interval_seconds)
    return None


async def process_due_schedules():
    """Polls database for due schedules and queues new tasks."""
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        stmt = select(TaskSchedule).where(
            TaskSchedule.is_enabled.is_(True),
            TaskSchedule.next_run_at <= now,
        )
        due_schedules = (await db.execute(stmt)).scalars().all()

        if not due_schedules:
            return

        mq_client = await get_rabbitmq_client()

        for schedule in due_schedules:
            # Per-schedule distributed lock prevents duplicate task spawning across replicas
            async with distributed_lock(f"schedule:{schedule.id}", timeout_seconds=15) as lock_acquired:
                if not lock_acquired:
                    continue

                task_id = str(uuid.uuid4())
                new_task = Task(
                    id=task_id,
                    user_id=schedule.user_id,
                    title=f"[Scheduled] {schedule.title}",
                    task_type=schedule.task_type,
                    payload=schedule.payload,
                    status=TaskStatus.QUEUED,
                    priority=schedule.priority,
                )
                db.add(new_task)

                schedule.last_run_at = now
                schedule.next_run_at = calculate_next_run(schedule)
                await db.commit()

                # Publish to RabbitMQ
                task_message = {
                    "id": new_task.id,
                    "user_id": new_task.user_id,
                    "title": new_task.title,
                    "task_type": new_task.task_type,
                    "payload": new_task.payload,
                    "priority": new_task.priority,
                    "max_retries": new_task.max_retries,
                    "current_attempt": 0,
                    "timeout_seconds": new_task.timeout_seconds,
                }
                await mq_client.publish_task(
                    task_payload=task_message,
                    priority=new_task.priority,
                    routing_key="task.scheduled",
                )
                SCHEDULED_TASKS_FIRED.inc()
                logger.info(f"Published scheduled task {task_id} for schedule '{schedule.title}'")


async def process_delayed_tasks():
    """Polls database for delayed tasks whose countdown has elapsed and enqueues them."""
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        stmt = select(Task).where(
            Task.status == TaskStatus.PENDING,
            Task.scheduled_at.is_not(None),
            Task.scheduled_at <= now,
        )
        due_tasks = (await db.execute(stmt)).scalars().all()
        if not due_tasks:
            return

        mq_client = await get_rabbitmq_client()
        for task in due_tasks:
            async with distributed_lock(f"task:delay:{task.id}", timeout_seconds=15) as lock_acquired:
                if not lock_acquired:
                    continue

                task.status = TaskStatus.QUEUED
                await db.commit()

                task_message = {
                    "id": task.id,
                    "user_id": task.user_id,
                    "title": task.title,
                    "task_type": task.task_type,
                    "payload": task.payload,
                    "priority": task.priority,
                    "max_retries": task.max_retries,
                    "current_attempt": task.current_attempt,
                    "timeout_seconds": task.timeout_seconds,
                    "trace_id": task.trace_id,
                    "webhook_url": task.webhook_url,
                    "delay_seconds": task.delay_seconds,
                }
                await mq_client.publish_task(
                    task_payload=task_message,
                    priority=task.priority,
                    routing_key="task.created",
                )
                logger.info(f"Delayed Task: Released task {task.id} to queue after scheduled countdown expired.")


async def run_scheduler():
    global running
    logger.info("Starting CloudTask Distributed Scheduler...")

    while running:
        try:
            # Leader election lock ensures only one active scheduler instance runs at a time
            async with distributed_lock("scheduler:leader", timeout_seconds=10) as is_leader:
                if is_leader:
                    SCHEDULER_LEADER_STATUS.set(1)
                    await process_due_schedules()
                    await process_delayed_tasks()
                else:
                    SCHEDULER_LEADER_STATUS.set(0)
        except Exception as e:
            SCHEDULER_ERRORS.inc()
            logger.error(f"Error during scheduler cycle: {e}")

        await asyncio.sleep(5)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    try:
        start_http_server(9092)
        logger.info("Scheduler metrics server started on port 9092")
    except Exception as e:
        logger.warning(f"Could not start metrics server: {e}")

    try:
        asyncio.run(run_scheduler())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler process stopped.")
