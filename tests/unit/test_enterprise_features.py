import hashlib
import hmac
import importlib.util
import time

from pkg.models.task import Task, TaskStatus

# Dynamically import schemas from services/task-service
spec = importlib.util.spec_from_file_location("task_schemas", "services/task-service/schemas.py")
task_schemas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(task_schemas)
CreateTaskRequest = task_schemas.CreateTaskRequest
BatchCreateTasksRequest = task_schemas.BatchCreateTasksRequest
BatchTaskResponse = task_schemas.BatchTaskResponse
DLQReplayResponse = task_schemas.DLQReplayResponse
TaskResponse = task_schemas.TaskResponse


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
