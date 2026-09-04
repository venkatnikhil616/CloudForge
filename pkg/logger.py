import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Dict

correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="")


def set_correlation_id(correlation_id: str) -> None:
    correlation_id_ctx.set(correlation_id)


def get_correlation_id() -> str:
    return correlation_id_ctx.get()


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter for Loki / OpenTelemetry compatibility."""

    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        log_data: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "service": self.service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }

        cid = get_correlation_id()
        if cid:
            log_data["correlation_id"] = cid

        if hasattr(record, "task_id"):
            log_data["task_id"] = record.task_id
        if hasattr(record, "event"):
            log_data["event"] = record.event
        if hasattr(record, "duration_ms"):
            log_data["duration_ms"] = record.duration_ms
        if hasattr(record, "extra_fields"):
            log_data.update(record.extra_fields)

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


def get_logger(service_name: str) -> logging.Logger:
    """Configures and returns a structured JSON logger for the service."""
    logger = logging.getLogger(service_name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter(service_name))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
