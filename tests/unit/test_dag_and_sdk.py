import importlib.util

from pkg.models.task import TaskStatus
from sdk.cloudtask.client import CloudTaskClient

# Dynamically import schemas from services/task-service
spec = importlib.util.spec_from_file_location("task_schemas", "services/task-service/schemas.py")
task_schemas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(task_schemas)
CreateTaskRequest = task_schemas.CreateTaskRequest
TaskResponse = task_schemas.TaskResponse


def test_create_task_request_dag_validation():
    req = CreateTaskRequest(
        title="Dependent Task",
        task_type="email_dispatch",
        depends_on=["task-parent-1", "task-parent-2"]
    )
    assert req.depends_on == ["task-parent-1", "task-parent-2"]
    assert req.priority == 5


def test_task_response_progress_and_trace():
    data = {
        "id": "task-123",
        "user_id": "user-456",
        "title": "Data Processing",
        "task_type": "data_processing",
        "payload": {},
        "status": TaskStatus.RUNNING,
        "priority": 8,
        "max_retries": 4,
        "current_attempt": 1,
        "timeout_seconds": 300,
        "progress": 75,
        "depends_on": ["parent-01"],
        "trace_id": "trace-abc12345",
        "created_at": "2026-09-04T08:00:00Z",
        "updated_at": "2026-09-04T08:01:00Z",
        "attempts": []
    }
    resp = TaskResponse(**data)
    assert resp.progress == 75
    assert resp.depends_on == ["parent-01"]
    assert resp.trace_id == "trace-abc12345"


def test_sdk_decorator_syntax():
    client = CloudTaskClient(base_url="http://localhost:8000", token="mock-token")
    
    @client.task(task_type="report_generation", priority=9)
    def my_task(month: str):
        pass

    assert hasattr(my_task, "delay")
