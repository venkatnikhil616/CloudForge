import hashlib
import hmac
import importlib.util
import time
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import delete

from pkg.database import AsyncSessionLocal
from pkg.models.task import Task, TaskStatus

# Dynamically import schemas and routes from services/task-service
spec = importlib.util.spec_from_file_location("task_schemas", "services/task-service/schemas.py")
task_schemas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(task_schemas)
CreateTaskRequest = task_schemas.CreateTaskRequest
BatchCreateTasksRequest = task_schemas.BatchCreateTasksRequest
BatchTaskResponse = task_schemas.BatchTaskResponse
DLQReplayResponse = task_schemas.DLQReplayResponse
TaskResponse = task_schemas.TaskResponse

routes_spec = importlib.util.spec_from_file_location("task_routes", "services/task-service/routes.py")
task_routes = importlib.util.module_from_spec(routes_spec)
routes_spec.loader.exec_module(task_routes)
create_task = task_routes.create_task
check_duplicate_endpoint = task_routes.check_duplicate_endpoint
get_duplicates_endpoint = task_routes.get_duplicates_endpoint


def test_batch_create_tasks_request_validation():
    batch_req = BatchCreateTasksRequest(
        tasks=[
            CreateTaskRequest(
                title="Batch Task 1",
                task_type="report_generation",
                priority=9,
                delay_seconds=30,
                webhook_url="https://api.myapp.com/webhook",
            ),
            CreateTaskRequest(
                title="Batch Task 2",
                task_type="data_processing",
                priority=5,
            )
        ]
    )
    assert len(batch_req.tasks) == 2
    assert batch_req.tasks[0].delay_seconds == 30
    assert batch_req.tasks[0].webhook_url == "https://api.myapp.com/webhook"
    assert batch_req.tasks[1].delay_seconds == 0


def test_dlq_replay_response_schema():
    resp = DLQReplayResponse(
        replayed_count=3,
        message="Successfully replayed 3 dead-lettered tasks",
        task_ids=["task-1", "task-2", "task-3"]
    )
    assert resp.replayed_count == 3
    assert len(resp.task_ids) == 3


def test_webhook_hmac_sha256_signing():
    secret = "test-webhook-secret-key"
    payload = b'{"event":"task.completed","status":"SUCCESS","task_id":"12345"}'
    timestamp = int(time.time())

    to_sign = f"{timestamp}.".encode("utf-8") + payload
    signature = hmac.new(secret.encode("utf-8"), to_sign, hashlib.sha256).hexdigest()
    header_val = f"t={timestamp},v1={signature}"

    assert header_val.startswith(f"t={timestamp},v1=")
    # Verify signature matches
    expected = hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode("utf-8") + payload, hashlib.sha256).hexdigest()
    assert signature == expected


def test_task_model_enterprise_fields():
    task = Task(
        id="test-enterprise-id",
        user_id="user-123",
        title="Enterprise Task",
        task_type="data_processing",
        payload={"foo": "bar"},
        status=TaskStatus.QUEUED,
        priority=8,
        webhook_url="https://api.example.com/callback",
        delay_seconds=60,
    )
    assert task.webhook_url == "https://api.example.com/callback"
    assert task.delay_seconds == 60
    assert task.status == TaskStatus.QUEUED


def test_create_task_request_prevent_duplicates_default():
    req = CreateTaskRequest(title="Unique Task", task_type="report_generation")
    assert req.prevent_duplicates is True

    req_override = CreateTaskRequest(
        title="Allow Duplicate Task",
        task_type="report_generation",
        prevent_duplicates=False,
    )
    assert req_override.prevent_duplicates is False


@pytest.mark.asyncio
async def test_duplicate_task_rejection_and_override():
    test_user = f"test-user-{uuid.uuid4().hex[:8]}"
    test_title = f"Dedup Test {uuid.uuid4().hex[:6]}"
    test_type = "report_generation"

    async with AsyncSessionLocal() as session:
        # Create an initial task
        req1 = CreateTaskRequest(
            title=test_title,
            task_type=test_type,
            priority=5,
            prevent_duplicates=True,
        )
        task1 = await create_task(
            req=req1,
            user_id=test_user,
            db=session,
        )
        assert task1.id is not None
        assert task1.status == TaskStatus.QUEUED

        # Attempt to create duplicate task with prevent_duplicates=True -> HTTP 409
        req2 = CreateTaskRequest(
            title=test_title,
            task_type=test_type,
            priority=7,
            prevent_duplicates=True,
        )
        with pytest.raises(HTTPException) as exc_info:
            await create_task(
                req=req2,
                user_id=test_user,
                db=session,
            )
        assert exc_info.value.status_code == 409
        assert "Duplicate task detected" in exc_info.value.detail
        assert task1.id in exc_info.value.detail

        # Check pre-flight verification endpoint
        check_res = await check_duplicate_endpoint(
            req=req2,
            user_id=test_user,
            db=session,
        )
        assert check_res["is_duplicate"] is True
        assert check_res["existing_task_id"] == task1.id
        assert check_res["status"] == TaskStatus.QUEUED

        # Check non-duplicate pre-flight check
        diff_req = CreateTaskRequest(
            title=f"Different Title {uuid.uuid4().hex[:6]}",
            task_type=test_type,
        )
        check_res_diff = await check_duplicate_endpoint(
            req=diff_req,
            user_id=test_user,
            db=session,
        )
        assert check_res_diff["is_duplicate"] is False

        # Create duplicate task with prevent_duplicates=False -> Success
        req_override = CreateTaskRequest(
            title=test_title,
            task_type=test_type,
            priority=8,
            prevent_duplicates=False,
        )
        task2 = await create_task(
            req=req_override,
            user_id=test_user,
            db=session,
        )
        assert task2.id is not None
        assert task2.id != task1.id

        # Clean up created tasks for this test
        await session.execute(delete(Task).where(Task.user_id == test_user))
        await session.commit()


@pytest.mark.asyncio
async def test_terminal_task_not_blocked():
    test_user = f"test-user-{uuid.uuid4().hex[:8]}"
    test_title = f"Terminal Task {uuid.uuid4().hex[:6]}"
    test_type = "data_processing"

    async with AsyncSessionLocal() as session:
        # Create a task in SUCCESS status
        terminal_task = Task(
            id=str(uuid.uuid4()),
            user_id=test_user,
            title=test_title,
            task_type=test_type,
            status=TaskStatus.SUCCESS,
            priority=5,
        )
        session.add(terminal_task)
        await session.commit()

        # Attempt to create task with same title/type and prevent_duplicates=True -> should succeed
        req = CreateTaskRequest(
            title=test_title,
            task_type=test_type,
            priority=5,
            prevent_duplicates=True,
        )
        new_task = await create_task(
            req=req,
            user_id=test_user,
            db=session,
        )
        assert new_task.id is not None
        assert new_task.id != terminal_task.id

        # Clean up
        await session.execute(delete(Task).where(Task.user_id == test_user))
        await session.commit()


@pytest.mark.asyncio
async def test_get_duplicates_endpoint():
    test_user = f"test-user-{uuid.uuid4().hex[:8]}"
    test_title = f"Dup Cluster {uuid.uuid4().hex[:6]}"
    test_type = "system_cleanup"

    async with AsyncSessionLocal() as session:
        # Create two tasks with identical title and type
        t1 = Task(
            id=str(uuid.uuid4()),
            user_id=test_user,
            title=test_title,
            task_type=test_type,
            status=TaskStatus.QUEUED,
            priority=5,
        )
        t2 = Task(
            id=str(uuid.uuid4()),
            user_id=test_user,
            title=test_title,
            task_type=test_type,
            status=TaskStatus.RUNNING,
            priority=5,
        )
        session.add_all([t1, t2])
        await session.commit()

        res = await get_duplicates_endpoint(user_id=test_user, db=session)
        assert res["status"] == "ok"
        assert res["duplicate_groups_count"] >= 1
        found = False
        for group in res["duplicates"]:
            if group["title"] == test_title and group["task_type"] == test_type:
                found = True
                assert group["count"] == 2
                break
        assert found, "Created duplicate group was not found in scanner results"

        # Clean up
        await session.execute(delete(Task).where(Task.user_id == test_user))
        await session.commit()

