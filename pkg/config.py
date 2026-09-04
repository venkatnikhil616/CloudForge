import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_database_url() -> str:
    """Ensures PostgreSQL URL is formatted properly for asyncpg, or falls back to local SQLite."""
    raw_url = os.getenv("DATABASE_URL")
    if raw_url:
        if raw_url.startswith("postgres://"):
            return raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif raw_url.startswith("postgresql://") and not raw_url.startswith("postgresql+asyncpg://"):
            return raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return raw_url

    postgres_host = os.getenv("POSTGRES_HOST")
    if postgres_host and postgres_host != "localhost":
        user = os.getenv("POSTGRES_USER", "cloudtask")
        password = os.getenv("POSTGRES_PASSWORD", "cloudtask_secret")
        port = os.getenv("POSTGRES_PORT", "5432")
        db = os.getenv("POSTGRES_DB", "cloudtask_db")
        return f"postgresql+asyncpg://{user}:{password}@{postgres_host}:{port}/{db}"

    # Default fallback when standalone / local without running Postgres
    return os.getenv("FALLBACK_DATABASE_URL", "sqlite+aiosqlite:///cloudtask.db")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Global
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # PostgreSQL (Auto-formats postgres:// from Render / Cloud providers to postgresql+asyncpg://)
    DATABASE_URL: str = _resolve_database_url()

    # Redis
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_URL: str = os.getenv("REDIS_URL", f"redis://{REDIS_HOST}:{REDIS_PORT}/0")

    # RabbitMQ
    RABBITMQ_USER: str = os.getenv("RABBITMQ_USER", "guest")
    RABBITMQ_PASSWORD: str = os.getenv("RABBITMQ_PASSWORD", "guest")
    RABBITMQ_HOST: str = os.getenv("RABBITMQ_HOST", "localhost")
    RABBITMQ_PORT: int = int(os.getenv("RABBITMQ_PORT", "5672"))
    RABBITMQ_URL: str = os.getenv(
        "RABBITMQ_URL",
        f"amqp://{RABBITMQ_USER}:{RABBITMQ_PASSWORD}@{RABBITMQ_HOST}:{RABBITMQ_PORT}/"
    )
    RABBITMQ_EXCHANGE: str = os.getenv("RABBITMQ_EXCHANGE", "cloudtask.events")
    RABBITMQ_DLX_EXCHANGE: str = os.getenv("RABBITMQ_DLX_EXCHANGE", "cloudtask.dlx")
    RABBITMQ_TASK_QUEUE: str = os.getenv("RABBITMQ_TASK_QUEUE", "cloudtask.tasks")
    RABBITMQ_DLQ_QUEUE: str = os.getenv("RABBITMQ_DLQ_QUEUE", "cloudtask.tasks.dlq")
    RABBITMQ_NOTIFICATION_QUEUE: str = os.getenv("RABBITMQ_NOTIFICATION_QUEUE", "cloudtask.notifications")

    # Security & JWT
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "super-secret-production-grade-encryption-key-change-me-in-prod")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

    # Dynamic Port Handling (Render passes $PORT for web service)
    API_GATEWAY_PORT: int = int(os.getenv("PORT", os.getenv("API_GATEWAY_PORT", "8000")))
    AUTH_SERVICE_PORT: int = int(os.getenv("AUTH_SERVICE_PORT", "8001"))
    TASK_SERVICE_PORT: int = int(os.getenv("TASK_SERVICE_PORT", "8002"))
    NOTIFICATION_SERVICE_PORT: int = int(os.getenv("NOTIFICATION_SERVICE_PORT", "8003"))
    METRICS_PORT: int = int(os.getenv("METRICS_PORT", "9090"))

    # Worker Settings
    WORKER_CONCURRENCY: int = int(os.getenv("WORKER_CONCURRENCY", "5"))
    WORKER_MAX_RETRIES: int = int(os.getenv("WORKER_MAX_RETRIES", "4"))
    WORKER_INITIAL_BACKOFF_SECONDS: int = int(os.getenv("WORKER_INITIAL_BACKOFF_SECONDS", "5"))


@lru_cache()
def get_settings() -> Settings:
    return Settings()
