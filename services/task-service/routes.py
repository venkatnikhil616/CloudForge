import asyncio
import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
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
    from .schemas import CreateTaskRequest, TaskListResponse, TaskResponse
except ImportError:
    from schemas import CreateTaskRequest, TaskListResponse, TaskResponse

logger = get_logger("task-service")
router = APIRouter(prefix="/tasks", tags=["Tasks"])


async def get_current_user_id(authorization: Optional[str] = Header(None)) -> str:
    """Helper to extract user_id from Authorization Bearer token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header with Bearer token is required"
        )
    token = authorization.split(" ")[1]
    try:
        payload = decode_access_token(token)
        return str(payload.get("sub"))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e


async def check_and_trigger_dependents(completed_task_id: str) -> None:
    """DAG Resolver: checks if any PENDING tasks were waiting on completed_task_id."""
    async with AsyncSessionLocal() as db:
        # Find all pending tasks
        stmt = select(Task).where(Task.status == TaskStatus.PENDING)
        pending_tasks = (await db.execute(stmt)).scalars().all()

        mq_client = None
        for p_task in pending_tasks:
            if completed_task_id in p_task.depends_on:
                # Check if ALL dependencies are now SUCCESS
                dep_stmt = select(Task).where(Task.id.in_(p_task.depends_on))
                dep_tasks = (await db.execute(dep_stmt)).scalars().all()
                
                all_succeeded = all(d.status == TaskStatus.SUCCESS for d in dep_tasks) and len(dep_tasks) == len(p_task.depends_on)
                if all_succeeded:
                    p_task.status = TaskStatus.QUEUED
                    await db.commit()

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
                    logger.info(f"DAG: Unlocked dependent task {p_task.id} after dependency {completed_task_id} completed.")


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

    # 2. Check DAG dependencies: are all prerequisites met?
    initial_status = TaskStatus.QUEUED
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
    )
    db.add(task)
    await db.commit()

    if req.idempotency_key:
        await store_idempotency(req.idempotency_key, task_id)

    # 3. Publish to RabbitMQ only if not blocked by DAG dependencies
    if initial_status == TaskStatus.QUEUED:
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
        }
        await mq_client.publish_task(
            task_payload=task_message,
            priority=task.priority,
            routing_key="task.created",
        )

    stmt = select(Task).options(selectinload(Task.attempts)).where(Task.id == task_id)
    created_task = (await db.execute(stmt)).scalar_one()
    return created_task


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status_filter: Optional[TaskStatus] = None,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db_session)
):
    offset = (page - 1) * limit
    base_query = select(Task).where(Task.user_id == user_id)
    count_query = select(func.count(Task.id)).where(Task.user_id == user_id)

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


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db_session)
):
    stmt = select(Task).options(selectinload(Task.attempts)).where(
        Task.id == task_id,
        Task.user_id == user_id
    )
    task = (await db.execute(stmt)).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


@router.get("/{task_id}/stream")
async def stream_task_progress(
    task_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db_session)
):
    """Server-Sent Events (SSE) endpoint to stream real-time task progress."""
    stmt = select(Task).where(Task.id == task_id, Task.user_id == user_id)
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

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(
    task_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db_session)
):
    stmt = select(Task).options(selectinload(Task.attempts)).where(
        Task.id == task_id,
        Task.user_id == user_id
    )
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
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db_session)
):
    stmt = select(Task).options(selectinload(Task.attempts)).where(
        Task.id == task_id,
        Task.user_id == user_id
    )
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
    await db.commit()

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
    }
    await mq_client.publish_task(
        task_payload=task_message,
        priority=task.priority,
        routing_key="task.created",
    )

    logger.info(f"Task {task_id} manually re-queued for execution")
    return task
