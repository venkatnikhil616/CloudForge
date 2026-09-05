from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from pkg.models.task import TaskStatus


class CreateTaskRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    task_type: str = Field(..., description="e.g. data_processing, email_dispatch, report_generation")
    payload: Dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=5, ge=1, le=10, description="1 (Low) to 10 (Critical)")
    max_retries: int = Field(default=4, ge=0, le=10)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    depends_on: List[str] = Field(default_factory=list, description="List of prerequisite task IDs (DAG execution)")
    idempotency_key: Optional[str] = Field(default=None, max_length=255)
    webhook_url: Optional[str] = Field(default=None, max_length=1000, description="HMAC-signed webhook callback URL upon completion or failure")
    delay_seconds: Optional[int] = Field(default=0, ge=0, le=86400, description="Delay execution countdown in seconds (AWS SQS pattern)")
    prevent_duplicates: Optional[bool] = Field(default=True, description="When true, detects and blocks duplicate active tasks with identical title and type")


class TaskAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    attempt_number: int
    status: str
    worker_id: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    trace_id: Optional[str] = None
    error_message: Optional[str] = None


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    title: str
    task_type: str
    payload: Dict[str, Any]
    status: TaskStatus
    priority: int
    max_retries: int
    current_attempt: int
    timeout_seconds: int
    progress: int = 0
    depends_on: List[str] = []
    trace_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    webhook_url: Optional[str] = None
    delay_seconds: Optional[int] = 0
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    attempts: List[TaskAttemptResponse] = []


class TaskListResponse(BaseModel):
    total: int
    page: int
    limit: int
    tasks: List[TaskResponse]


class BatchCreateTasksRequest(BaseModel):
    tasks: List[CreateTaskRequest] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Batch list of tasks (max 100 per request, AWS SQS pattern)"
    )


class BatchTaskResponse(BaseModel):
    total_submitted: int
    successful_count: int
    failed_count: int
    tasks: List[TaskResponse]
    errors: List[Dict[str, Any]] = Field(default_factory=list)


class DLQReplayResponse(BaseModel):
    replayed_count: int
    message: str
    task_ids: List[str]
