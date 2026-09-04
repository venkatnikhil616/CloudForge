import time
from typing import Any, Callable, Dict, List, Optional

import httpx


class TaskPromise:
    """Represents a submitted asynchronous task with helpers to poll and wait for results."""

    def __init__(self, client: "CloudTaskClient", task_id: str):
        self.client = client
        self.task_id = task_id

    def get_status(self) -> Dict[str, Any]:
        return self.client.get_task(self.task_id)

    def wait(self, poll_interval: float = 1.0, timeout: float = 60.0) -> Dict[str, Any]:
        """Blocks until the task completes (SUCCESS, FAILED, or DEAD_LETTERED)."""
        start = time.time()
        while time.time() - start < timeout:
            data = self.get_status()
            status = data.get("status")
            if status in ["SUCCESS", "FAILED", "DEAD_LETTERED", "CANCELLED"]:
                return data
            time.sleep(poll_interval)
        raise TimeoutError(f"Task {self.task_id} timed out waiting for result after {timeout}s")


class CloudTaskClient:
    """Production Python Client for the CloudTask Distributed Platform."""

    def __init__(
        self,
        base_url: str = "https://cloudtask-platform.onrender.com",
        email: Optional[str] = None,
        password: Optional[str] = None,
        token: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._http = httpx.Client(base_url=self.base_url, timeout=30.0)

        if not self.token and email and password:
            self.login(email, password)

    def login(self, email: str, password: str) -> str:
        """Authenticates with API Gateway and stores bearer token."""
        resp = self._http.post("/api/v1/auth/login", json={"email": email, "password": password})
        resp.raise_for_status()
        self.token = resp.json()["access_token"]
        return self.token

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def submit_task(
        self,
        title: str,
        task_type: str,
        payload: Dict[str, Any],
        priority: int = 5,
        max_retries: int = 4,
        depends_on: Optional[List[str]] = None,
        idempotency_key: Optional[str] = None,
        webhook_url: Optional[str] = None,
        delay_seconds: Optional[int] = 0,
    ) -> TaskPromise:
        """Enqueues an asynchronous task to the CloudTask cluster."""
        body = {
            "title": title,
            "task_type": task_type,
            "payload": payload,
            "priority": priority,
            "max_retries": max_retries,
            "depends_on": depends_on or [],
            "idempotency_key": idempotency_key,
            "webhook_url": webhook_url,
            "delay_seconds": delay_seconds or 0,
        }
        resp = self._http.post("/api/v1/tasks", json=body, headers=self._headers())
        resp.raise_for_status()
        task_id = resp.json()["id"]
        return TaskPromise(self, task_id)

    def submit_batch(self, tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """AWS SQS-style Batch Task Ingestion: Submits up to 100 tasks in a single request."""
        resp = self._http.post("/api/v1/tasks/batch", json={"tasks": tasks}, headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    def replay_dlq(self) -> Dict[str, Any]:
        """AWS SQS Dead-Letter Queue Redrive: Replays all failed or dead-lettered tasks."""
        resp = self._http.post("/api/v1/tasks/dlq/replay-all", headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    def export_audit(self, format: str = "csv") -> bytes:
        """Downloads RFC 4180 CSV or JSON audit records of task execution history."""
        resp = self._http.get(f"/api/v1/tasks/export?format={format}", headers=self._headers())
        resp.raise_for_status()
        return resp.content

    def get_task(self, task_id: str) -> Dict[str, Any]:
        """Fetches status, progress, and result of a task."""
        resp = self._http.get(f"/api/v1/tasks/{task_id}", headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    def cancel_task(self, task_id: str) -> Dict[str, Any]:
        """Aborts a queued or running task via preemption."""
        resp = self._http.post(f"/api/v1/tasks/{task_id}/cancel", headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    def retry_task(self, task_id: str) -> Dict[str, Any]:
        """Manually retries a failed or dead-lettered task."""
        resp = self._http.post(f"/api/v1/tasks/{task_id}/retry", headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    def task(self, task_type: str, priority: int = 5, max_retries: int = 4):
        """Decorator to turn Python functions into distributed tasks."""
        def decorator(func: Callable):
            def delay(*args, **kwargs) -> TaskPromise:
                title = f"Task: {func.__name__}"
                payload = kwargs
                return self.submit_task(
                    title=title,
                    task_type=task_type,
                    payload=payload,
                    priority=priority,
                    max_retries=max_retries,
                )
            func.delay = delay
            return func
        return decorator
