from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from pkg.models.task import TaskStatus


class CreateTaskRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    task_type: str = Field(..., description="e.g. data_processing, email_dispatch, report_generation")
    payload: Dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=5, ge=1, le=10, description="1 (Low) to 10 (Critical)")
    max_retries: int = Field(default=4, ge=0, le=10)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    idempotency_key: Optional[str] = Field(default=None, max_length=255)


class TaskAttemptResponse(BaseModel):
    id: str
    attempt_number: int
    status: str
    worker_id: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None

    class Config:
        from_attributes = True


class TaskResponse(BaseModel):
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
    idempotency_key: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    attempts: List[TaskAttemptResponse] = []

    class Config:
        from_attributes = True


class TaskListResponse(BaseModel):
    total: int
    page: int
    limit: int
    tasks: List[TaskResponse]
