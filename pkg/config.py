import os
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Global
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # PostgreSQL
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "cloudtask")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "cloudtask_secret")
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "cloudtask_db")
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
    )

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

    # Ports
    API_GATEWAY_PORT: int = int(os.getenv("API_GATEWAY_PORT", "8000"))
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
