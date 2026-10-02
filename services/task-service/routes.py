import asyncio
import csv
import io
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pkg.database import AsyncSessionLocal, get_db_session
from pkg.logger import get_logger
from pkg.messaging import get_rabbitmq_client
from pkg.models.task import Task, TaskStatus
from pkg.redis_client import check_idempotency, get_redis_client, store_idempotency
from pkg.security import decode_access_token

try:
    from .schemas import (
        BatchCreateTasksRequest,
        BatchTaskResponse,
        CreateTaskRequest,
        DLQReplayResponse,
        TaskListResponse,
        TaskResponse,
    )
except (ImportError, Exception):
    import sys
    from pathlib import Path
    _svc_dir = str(Path(__file__).resolve().parent)
    if _svc_dir not in sys.path:
        sys.path.insert(0, _svc_dir)
    from schemas import (
        BatchCreateTasksRequest,
        BatchTaskResponse,
        CreateTaskRequest,
        DLQReplayResponse,
        TaskListResponse,
        TaskResponse,
    )

logger = get_logger("task-service")
router = APIRouter(prefix="/tasks", tags=["Tasks"])


async def get_current_user_claims(
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None),
) -> dict:
    """Helper to extract user claims from Authorization Bearer token or query parameter."""
    raw_token = token
    if authorization and authorization.startswith("Bearer "):
        raw_token = authorization.split(" ")[1]
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header with Bearer token is required"
        )
    try:
        return decode_access_token(raw_token)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e


async def get_current_user_id(
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None),
) -> str:
    """Helper to extract user_id from Authorization Bearer token or query parameter."""
    claims = await get_current_user_claims(authorization=authorization, token=token)
    return str(claims.get("sub"))



@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    req: CreateTaskRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db_session)
):
    # 1. Check idempotency if key provided
    if req.idempotency_key:
        cached_id = await check_idempotency(req.idempotency_key)
        if cached_id:
            stmt = select(Task).options(selectinload(Task.attempts)).where(Task.id == cached_id)
            existing = (await db.execute(stmt)).scalar_one_or_none()
            if existing:
                logger.info(f"Returning cached task {cached_id} for idempotency key {req.idempotency_key}")
                return existing

    # 2. Duplicate Detection: Check if active task with identical title and type already exists
    if getattr(req, "prevent_duplicates", True):
        dup_stmt = (
            select(Task)
            .where(
                Task.user_id == user_id,
                Task.title == req.title,
                Task.task_type == req.task_type,
                Task.status.in_([TaskStatus.QUEUED, TaskStatus.PENDING, TaskStatus.RUNNING]),
            )
            .limit(1)
        )
        existing_dup = (await db.execute(dup_stmt)).scalar_one_or_none()
        if existing_dup:
            logger.warning(
                f"Duplicate task rejected: '{req.title}' ({req.task_type}) already active as {existing_dup.status} ({existing_dup.id})"
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Duplicate task detected: A task with title '{req.title}' and type '{req.task_type}' is already active in state '{existing_dup.status}' (Task ID: {existing_dup.id})."
            )

    # 3. Check DAG dependencies and delayed execution
    initial_status = TaskStatus.QUEUED
    scheduled_at = None
    if req.delay_seconds and req.delay_seconds > 0:
        initial_status = TaskStatus.PENDING
        scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=req.delay_seconds)
    if req.depends_on:
        dep_stmt = select(Task).where(Task.id.in_(req.depends_on))
        dep_tasks = (await db.execute(dep_stmt)).scalars().all()
        all_met = all(d.status == TaskStatus.SUCCESS for d in dep_tasks) and len(dep_tasks) == len(req.depends_on)
        if not all_met:
            initial_status = TaskStatus.PENDING

    task_id = str(uuid.uuid4())
    trace_id = f"trace-{uuid.uuid4().hex[:16]}"
    task = Task(
        id=task_id,
        user_id=user_id,
        title=req.title,
        task_type=req.task_type,
        payload=req.payload,
        status=initial_status,
        priority=req.priority,
        max_retries=req.max_retries,
        timeout_seconds=req.timeout_seconds,
        progress=0,
        depends_on=req.depends_on,
        trace_id=trace_id,
        idempotency_key=req.idempotency_key,
        webhook_url=req.webhook_url,
        delay_seconds=req.delay_seconds or 0,
        scheduled_at=scheduled_at,
    )
    db.add(task)
    await db.commit()

    if req.idempotency_key:
        await store_idempotency(req.idempotency_key, task_id)

    # 3. Publish to RabbitMQ only if not blocked by DAG or scheduled delay, and mode is auto
    from pkg.redis_client import get_execution_mode
    current_mode = await get_execution_mode()

    if initial_status == TaskStatus.QUEUED and current_mode == "auto":
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
        try:
            mq_client = await get_rabbitmq_client()
            await mq_client.publish_task(
                task_payload=task_message,
                priority=task.priority,
                routing_key="task.created",
            )
        except Exception as e:
            logger.warning(f"RabbitMQ publish deferred/unavailable: {e}. Executing directly via local worker.")
            try:
                from services.worker.executor import execute_task
                asyncio.create_task(execute_task(task_message))
            except Exception as ex_exec:
                logger.warning(f"Direct worker fallback failed: {ex_exec}")
    elif initial_status == TaskStatus.QUEUED:
        logger.info(f"Task {task.id} stored in database in manual/staged mode (Priority {task.priority}). Awaiting Start Processing trigger.")


    stmt = select(Task).options(selectinload(Task.attempts)).where(Task.id == task_id)
    created_task = (await db.execute(stmt)).scalar_one()
    return created_task


@router.post("/batch", response_model=BatchTaskResponse, status_code=status.HTTP_201_CREATED)
async def create_batch_tasks(
    req: BatchCreateTasksRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db_session)
):
    """
    AWS SQS-style Batch Task Ingestion:
    Enqueues up to 100 tasks in a single network round-trip.
    Provides atomic registration, per-task idempotency, and bulk RabbitMQ dispatch.
    """
    created_tasks = []
    queued_messages = []
    errors = []

    for idx, t_req in enumerate(req.tasks):
        if t_req.idempotency_key:
            cached_id = await check_idempotency(t_req.idempotency_key)
            if cached_id:
                stmt = select(Task).options(selectinload(Task.attempts)).where(Task.id == cached_id)
                existing = (await db.execute(stmt)).scalar_one_or_none()
                if existing:
                    created_tasks.append(existing)
                    continue

        if getattr(t_req, "prevent_duplicates", True):
            dup_stmt = (
                select(Task)
                .where(
                    Task.user_id == user_id,
                    Task.title == t_req.title,
                    Task.task_type == t_req.task_type,
                    Task.status.in_([TaskStatus.QUEUED, TaskStatus.PENDING, TaskStatus.RUNNING]),
                )
                .limit(1)
            )
            existing_dup = (await db.execute(dup_stmt)).scalar_one_or_none()
            if existing_dup:
                logger.warning(
                    f"Batch task duplicate rejected: '{t_req.title}' ({t_req.task_type}) already active as {existing_dup.status} ({existing_dup.id})"
                )
                errors.append({
                    "index": idx,
                    "title": t_req.title,
                    "error": f"Duplicate task detected: already active in state '{existing_dup.status}' (Task ID: {existing_dup.id})"
                })
                continue

            if any(t.title == t_req.title and t.task_type == t_req.task_type for t in created_tasks):
                logger.warning(f"Batch task duplicate within request rejected: '{t_req.title}' ({t_req.task_type})")
                errors.append({
                    "index": idx,
                    "title": t_req.title,
                    "error": f"Duplicate task detected in batch request: '{t_req.title}'"
                })
                continue

        initial_status = TaskStatus.QUEUED
        scheduled_at = None
        if t_req.delay_seconds and t_req.delay_seconds > 0:
            initial_status = TaskStatus.PENDING
            scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=t_req.delay_seconds)
        if t_req.depends_on:
            dep_stmt = select(Task).where(Task.id.in_(t_req.depends_on))
            dep_tasks = (await db.execute(dep_stmt)).scalars().all()
            all_met = all(d.status == TaskStatus.SUCCESS for d in dep_tasks) and len(dep_tasks) == len(t_req.depends_on)
            if not all_met:
                initial_status = TaskStatus.PENDING

        task_id = str(uuid.uuid4())
        trace_id = f"trace-{uuid.uuid4().hex[:16]}"
        task = Task(
            id=task_id,
            user_id=user_id,
            title=t_req.title,
            task_type=t_req.task_type,
            payload=t_req.payload,
            status=initial_status,
            priority=t_req.priority,
            max_retries=t_req.max_retries,
            timeout_seconds=t_req.timeout_seconds,
            progress=0,
            depends_on=t_req.depends_on,
            trace_id=trace_id,
            idempotency_key=t_req.idempotency_key,
            webhook_url=t_req.webhook_url,
            delay_seconds=t_req.delay_seconds or 0,
            scheduled_at=scheduled_at,
        )
        db.add(task)
        created_tasks.append(task)

        if t_req.idempotency_key:
            await store_idempotency(t_req.idempotency_key, task_id)

        if initial_status == TaskStatus.QUEUED:
            queued_messages.append({
                "payload": {
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
                },
                "priority": task.priority
            })

    await db.commit()

    from pkg.redis_client import get_execution_mode
    current_mode = await get_execution_mode()

    if queued_messages and current_mode == "auto":
        try:
            mq_client = await get_rabbitmq_client()
            for msg in queued_messages:
                await mq_client.publish_task(
                    task_payload=msg["payload"],
                    priority=msg["priority"],
                    routing_key="task.created",
                )
        except Exception as e:
            logger.warning(f"Batch RabbitMQ publish deferred/unavailable: {e}. Executing directly via local worker.")
            try:
                from services.worker.executor import execute_task
                for msg in queued_messages:
                    asyncio.create_task(execute_task(msg["payload"]))
            except Exception as ex_exec:
                logger.warning(f"Batch direct worker fallback failed: {ex_exec}")
    elif queued_messages:
        logger.info(f"{len(queued_messages)} batch tasks stored in database in manual/staged mode. Awaiting Start Processing trigger.")

    task_ids = [t.id for t in created_tasks]
    stmt = select(Task).options(selectinload(Task.attempts)).where(Task.id.in_(task_ids))
    refreshed_tasks = (await db.execute(stmt)).scalars().all() if task_ids else []

    return BatchTaskResponse(
        total_submitted=len(req.tasks),
        successful_count=len(refreshed_tasks),
        failed_count=len(errors),
        tasks=list(refreshed_tasks),
        errors=errors
    )


@router.post("/dlq/replay-all", response_model=DLQReplayResponse)
async def replay_all_dlq_tasks(
    claims: Optional[dict] = Depends(get_current_user_claims),
    user_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session)
):
    """
    AWS SQS Dead-Letter Queue Redrive pattern:
    Bulk replays all DEAD_LETTERED or FAILED tasks back into the active queue.
    """
    if user_id is None and isinstance(claims, dict):
        user_id = str(claims.get("sub"))
        user_role = claims.get("role", "user")
    else:
        user_role = "admin" if user_id in ("admin", "admin-default") else "user"

    stmt = select(Task).where(Task.status.in_([TaskStatus.DEAD_LETTERED, TaskStatus.FAILED]))
    if user_role != "admin" and user_id:
        stmt = stmt.where(Task.user_id == user_id)
    tasks = (await db.execute(stmt)).scalars().all()
    if not tasks:
        return DLQReplayResponse(replayed_count=0, message="No dead-lettered or failed tasks found to replay", task_ids=[])

    mq_client = None
    try:
        mq_client = await get_rabbitmq_client()
    except Exception as e:
        logger.warning(f"RabbitMQ unavailable for DLQ replay: {e}")

    task_ids = []
    for task in tasks:
        task.status = TaskStatus.QUEUED
        task.error_message = None
        task.progress = 0
        task.current_attempt = 0
        task_ids.append(task.id)

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
        if mq_client:
            try:
                await mq_client.publish_task(
                    task_payload=task_message,
                    priority=task.priority,
                    routing_key="task.created",
                )
            except Exception as e:
                logger.warning(f"RabbitMQ publish deferred in DLQ replay for task {task.id}: {e}")

    await db.commit()
    logger.info(f"Redrive: Replayed {len(tasks)} DLQ tasks for user {user_id}")
    return DLQReplayResponse(
        replayed_count=len(tasks),
        message=f"Successfully replayed {len(tasks)} dead-lettered tasks back to execution queue",
        task_ids=task_ids
    )


@router.get("/export")
async def export_tasks(
    format: str = Query("csv", pattern="^(csv|json)$"),
    status_filter: Optional[TaskStatus] = None,
    claims: Optional[dict] = Depends(get_current_user_claims),
    user_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session)
):
    """
    Enterprise Compliance & Audit Export:
    Generates SOC2 / ISO27001 audit exports in RFC 4180 CSV or JSON format.
    """
    if user_id is None and isinstance(claims, dict):
        user_id = str(claims.get("sub"))
        user_role = claims.get("role", "user")
    else:
        user_role = "admin" if (user_id in ("admin", "admin-default") or user_id is None) else "user"

    query = select(Task).options(selectinload(Task.attempts))
    if user_role != "admin" and user_id:
        query = query.where(Task.user_id == user_id)
    if status_filter:
        query = query.where(Task.status == status_filter)
    query = query.order_by(Task.created_at.desc())
    tasks = (await db.execute(query)).scalars().all()

    if format == "json":
        task_dicts = [
            {
                "id": t.id,
                "title": t.title,
                "task_type": t.task_type,
                "status": t.status.value,
                "priority": t.priority,
                "current_attempt": t.current_attempt,
                "max_retries": t.max_retries,
                "trace_id": t.trace_id,
                "webhook_url": t.webhook_url,
                "delay_seconds": t.delay_seconds,
                "error_message": t.error_message,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
            for t in tasks
        ]
        return JSONResponse(content={"total": len(tasks), "tasks": task_dicts})

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "task_id", "title", "task_type", "status", "priority",
        "current_attempt", "max_retries", "trace_id", "webhook_url",
        "delay_seconds", "error_message", "created_at", "updated_at"
    ])
    for t in tasks:
        writer.writerow([
            t.id,
            t.title,
            t.task_type,
            t.status.value,
            t.priority,
            t.current_attempt,
            t.max_retries,
            t.trace_id or "",
            t.webhook_url or "",
            t.delay_seconds or 0,
            t.error_message or "",
            t.created_at.isoformat() if t.created_at else "",
            t.updated_at.isoformat() if t.updated_at else "",
        ])

    csv_data = output.getvalue()
    filename = f"cloudtask_audit_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status_filter: Optional[TaskStatus] = None,
    claims: Optional[dict] = Depends(get_current_user_claims),
    user_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session)
):
    if user_id is None and isinstance(claims, dict):
        user_id = str(claims.get("sub"))
        user_role = claims.get("role", "user")
    else:
        user_role = "admin" if (user_id in ("admin", "admin-default") or user_id is None) else "user"

    offset = (page - 1) * limit
    base_query = select(Task)
    count_query = select(func.count(Task.id))

    # Only filter by user_id for regular users; admins see the full fleet
    if user_role != "admin" and user_id:
        base_query = base_query.where(Task.user_id == user_id)
        count_query = count_query.where(Task.user_id == user_id)

    if status_filter:
        base_query = base_query.where(Task.status == status_filter)
        count_query = count_query.where(Task.status == status_filter)

    total = (await db.execute(count_query)).scalar_one()
    stmt = (
        base_query.options(selectinload(Task.attempts))
        .order_by(Task.priority.desc(), Task.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    tasks = (await db.execute(stmt)).scalars().all()

    return TaskListResponse(total=total, page=page, limit=limit, tasks=list(tasks))


@router.get("/execution-mode")
async def get_execution_mode_endpoint(db: AsyncSession = Depends(get_db_session)):
    """Returns current task processing mode ('manual' or 'auto') and queued count."""
    from pkg.redis_client import get_execution_mode
    mode = await get_execution_mode()
    stmt = select(func.count(Task.id)).where(Task.status == TaskStatus.QUEUED)
    queued_count = (await db.execute(stmt)).scalar() or 0
    return {"mode": mode, "queued_count": queued_count}


@router.post("/execution-mode")
async def set_execution_mode_endpoint(request: Request, db: AsyncSession = Depends(get_db_session)):
    """Switches task processing mode between 'manual' (batch staging) and 'auto' (instant)."""
    from pkg.redis_client import set_execution_mode
    try:
        body = await request.json()
        new_mode = body.get("mode", "auto")
    except Exception:
        new_mode = "auto"
    saved_mode = await set_execution_mode(new_mode)
    queued_count = 0
    if saved_mode == "auto":
        from services.worker.main import process_priority_queue
        stmt = select(func.count(Task.id)).where(Task.status.in_([TaskStatus.QUEUED, TaskStatus.PENDING]))
        queued_count = (await db.execute(stmt)).scalar() or 0
        if queued_count > 0:
            asyncio.create_task(process_priority_queue())
    return {"mode": saved_mode, "status": "updated", "queued_count": queued_count}


@router.post("/start-processing")
async def start_processing_endpoint(db: AsyncSession = Depends(get_db_session)):
    """
    Triggers execution of all currently QUEUED or ready PENDING tasks strictly in Priority Order (P10 -> P1).
    Tasks are processed sequentially with visual pacing to clearly demonstrate priority scheduling.
    """
    from services.worker.main import process_priority_queue

    now = datetime.now(timezone.utc)
    stmt = (
        select(Task)
        .where(Task.status.in_([TaskStatus.QUEUED, TaskStatus.PENDING]))
        .order_by(Task.priority.desc(), Task.created_at.asc())
    )
    all_waiting = (await db.execute(stmt)).scalars().all()

    waiting_tasks = []
    for t in all_waiting:
        if t.status == TaskStatus.QUEUED:
            waiting_tasks.append(t)
        elif t.status == TaskStatus.PENDING:
            sched = t.scheduled_at
            if sched and sched.tzinfo is None:
                sched = sched.replace(tzinfo=timezone.utc)
            if not t.depends_on and (not sched or sched <= now):
                t.status = TaskStatus.QUEUED
                waiting_tasks.append(t)

    if not waiting_tasks:
        return {"status": "idle", "message": "No tasks currently waiting in the queue.", "queued_count": 0}

    await db.commit()

    # Launch priority execution in background task
    asyncio.create_task(process_priority_queue())

    sequence = [{"id": t.id, "title": t.title, "priority": t.priority} for t in waiting_tasks]
    return {
        "status": "started",
        "message": f"Started processing {len(waiting_tasks)} tasks strictly in descending priority order (P10 -> P1).",
        "queued_count": len(waiting_tasks),
        "priority_order": sequence,
    }


@router.post("/clear-history")
@router.delete("/history")
async def clear_history_endpoint(
    claims: Optional[dict] = Depends(get_current_user_claims),
    user_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session)
):
    """
    Clears all historical tasks with status SUCCESS or CANCELLED from the database.
    Also cascades deletions to associated worker attempt records.
    """
    from sqlalchemy import delete

    from pkg.models.attempt import TaskAttempt

    if user_id is None and isinstance(claims, dict):
        user_id = str(claims.get("sub"))
        user_role = claims.get("role", "user")
    else:
        user_role = "admin" if (user_id is None or user_id in ("admin", "admin-default")) else "user"

    stmt = select(Task.id).where(Task.status.in_([TaskStatus.SUCCESS, TaskStatus.CANCELLED]))
    if user_role != "admin" and user_id:
        stmt = stmt.where(Task.user_id == user_id)
    task_ids = (await db.execute(stmt)).scalars().all()

    if not task_ids:
        return {"status": "ok", "deleted_count": 0, "message": "No historical tasks to clear."}

    # Clean up associated attempt logs and tasks
    await db.execute(delete(TaskAttempt).where(TaskAttempt.task_id.in_(task_ids)))
    del_stmt = delete(Task).where(Task.id.in_(task_ids))
    if user_role != "admin" and user_id:
        del_stmt = del_stmt.where(Task.user_id == user_id)
    await db.execute(del_stmt)
    await db.commit()

    logger.info(f"Cleared {len(task_ids)} completed/cancelled tasks from history (Requested by user {user_id}, role: {user_role}).")
    return {
        "status": "ok",
        "deleted_count": len(task_ids),
        "message": f"Successfully cleared {len(task_ids)} historical tasks."
    }


@router.post("/check-duplicate")
async def check_duplicate_endpoint(
    req: CreateTaskRequest,
    claims: Optional[dict] = Depends(get_current_user_claims),
    user_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session)
):
    """
    Pre-flight verification to detect if an active duplicate task exists with the given title and type.
    """
    if user_id is None and isinstance(claims, dict):
        user_id = str(claims.get("sub"))
        user_role = claims.get("role", "user")
    else:
        user_role = "admin" if user_id in ("admin", "admin-default") else "user"

    dup_stmt = (
        select(Task)
        .where(
            Task.title == req.title,
            Task.task_type == req.task_type,
            Task.status.in_([TaskStatus.QUEUED, TaskStatus.PENDING, TaskStatus.RUNNING]),
        )
    )
    if user_role != "admin" and user_id:
        dup_stmt = dup_stmt.where(Task.user_id == user_id)
    dup_stmt = dup_stmt.limit(1)
    existing_dup = (await db.execute(dup_stmt)).scalar_one_or_none()
    if existing_dup:
        return {
            "is_duplicate": True,
            "existing_task_id": existing_dup.id,
            "status": existing_dup.status,
            "title": existing_dup.title,
            "task_type": existing_dup.task_type,
            "message": f"An active duplicate task already exists in status '{existing_dup.status}' (Task ID: {existing_dup.id}).",
        }
    return {"is_duplicate": False, "message": "No active duplicate task found."}


@router.get("/duplicates")
async def get_duplicates_endpoint(
    claims: Optional[dict] = Depends(get_current_user_claims),
    user_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session)
):
    """
    Scans the cluster and returns all groups of duplicate tasks (sharing the same title and task_type).
    """
    if user_id is None and isinstance(claims, dict):
        user_id = str(claims.get("sub"))
        user_role = claims.get("role", "user")
    else:
        user_role = "admin" if user_id in ("admin", "admin-default") else "user"

    stmt = (
        select(Task)
        .where(Task.status.in_([TaskStatus.QUEUED, TaskStatus.PENDING, TaskStatus.RUNNING]))
        .order_by(Task.created_at.asc())
    )
    if user_role != "admin" and user_id != "admin-default" and user_id != "system":
        stmt = stmt.where(Task.user_id == user_id)

    tasks = (await db.execute(stmt)).scalars().all()

    groups: dict = {}
    for t in tasks:
        sig = f"{t.task_type}::{t.title}"
        if sig not in groups:
            groups[sig] = []
        groups[sig].append({
            "id": t.id,
            "title": t.title,
            "task_type": t.task_type,
            "status": t.status,
            "priority": t.priority,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        })

    duplicates = [
        {"signature": sig, "title": items[0]["title"], "task_type": items[0]["task_type"], "count": len(items), "tasks": items}
        for sig, items in groups.items()
        if len(items) > 1
    ]

    return {
        "status": "ok",
        "duplicate_groups_count": len(duplicates),
        "total_duplicate_tasks": sum(d["count"] for d in duplicates),
        "duplicates": duplicates,
    }




@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    claims: Optional[dict] = Depends(get_current_user_claims),
    user_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session)
):
    if user_id is None and isinstance(claims, dict):
        user_id = str(claims.get("sub"))
        user_role = claims.get("role", "user")
    else:
        user_role = "admin" if user_id in ("admin", "admin-default") else "user"

    stmt = select(Task).options(selectinload(Task.attempts)).where(Task.id == task_id)
    if user_role != "admin" and user_id:
        stmt = stmt.where(Task.user_id == user_id)
    task = (await db.execute(stmt)).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


@router.get("/{task_id}/stream")
async def stream_task_progress(
    task_id: str,
    claims: Optional[dict] = Depends(get_current_user_claims),
    user_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session)
):
    """Server-Sent Events (SSE) endpoint to stream real-time task progress."""
    if user_id is None and isinstance(claims, dict):
        user_id = str(claims.get("sub"))
        user_role = claims.get("role", "user")
    else:
        user_role = "admin" if user_id in ("admin", "admin-default") else "user"

    stmt = select(Task).where(Task.id == task_id)
    if user_role != "admin" and user_id:
        stmt = stmt.where(Task.user_id == user_id)
    task = (await db.execute(stmt)).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    async def event_generator():
        redis = get_redis_client()
        pubsub = redis.pubsub()
        channel_name = f"task:progress:{task_id}"
        await pubsub.subscribe(channel_name)

        try:
            # Yield initial state
            yield f"data: {json.dumps({'status': task.status.value, 'progress': task.progress, 'message': 'Subscribed to task stream'})}\n\n"

            timeout = 180  # 3 minutes max streaming
            start = asyncio.get_event_loop().time()

            while (asyncio.get_event_loop().time() - start) < timeout:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message.get("data"):
                    yield f"data: {message['data']}\n\n"
                    data_obj = json.loads(message["data"])
                    if data_obj.get("status") in ["SUCCESS", "FAILED", "DEAD_LETTERED", "CANCELLED"]:
                        break
                await asyncio.sleep(0.5)
        finally:
            await pubsub.unsubscribe(channel_name)
            await pubsub.close()

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(
    task_id: str,
    claims: Optional[dict] = Depends(get_current_user_claims),
    user_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session)
):
    if user_id is None and isinstance(claims, dict):
        user_id = str(claims.get("sub"))
        user_role = claims.get("role", "user")
    else:
        user_role = "admin" if user_id in ("admin", "admin-default") else "user"

    stmt = select(Task).options(selectinload(Task.attempts)).where(Task.id == task_id)
    if user_role != "admin" and user_id:
        stmt = stmt.where(Task.user_id == user_id)
    task = (await db.execute(stmt)).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    if task.status in [TaskStatus.SUCCESS, TaskStatus.DEAD_LETTERED, TaskStatus.CANCELLED]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel task in {task.status.value} state"
        )

    task.status = TaskStatus.CANCELLED
    await db.commit()
    await db.refresh(task)

    # Worker Preemption: Broadcast abort signal via Redis pub/sub to interrupt running worker immediately
    try:
        redis = get_redis_client()
        await redis.publish(f"task:abort:{task_id}", json.dumps({"action": "ABORT", "task_id": task_id}))
    except Exception as e:
        logger.warning(f"Failed to publish abort signal for task {task_id}: {e}")

    logger.info(f"Task {task_id} cancelled by user, abort signal emitted.")
    return task


@router.post("/{task_id}/retry", response_model=TaskResponse)
async def retry_task(
    task_id: str,
    claims: Optional[dict] = Depends(get_current_user_claims),
    user_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session)
):
    if user_id is None and isinstance(claims, dict):
        user_id = str(claims.get("sub"))
        user_role = claims.get("role", "user")
    else:
        user_role = "admin" if user_id in ("admin", "admin-default") else "user"

    stmt = select(Task).options(selectinload(Task.attempts)).where(Task.id == task_id)
    if user_role != "admin" and user_id:
        stmt = stmt.where(Task.user_id == user_id)
    task = (await db.execute(stmt)).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    if task.status not in [TaskStatus.FAILED, TaskStatus.DEAD_LETTERED]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only FAILED or DEAD_LETTERED tasks can be retried manually"
        )

    task.status = TaskStatus.QUEUED
    task.error_message = None
    task.progress = 0
    task.current_attempt = 0
    await db.commit()

    try:
        mq_client = await get_rabbitmq_client()
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
    except Exception as e:
        logger.warning(f"RabbitMQ publish deferred for retried task {task.id}: {e}")

    logger.info(f"Task {task_id} manually re-queued for execution")
    return task


