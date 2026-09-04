import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from pkg.config import get_settings
from pkg.database import check_database_health
from pkg.logger import get_logger, set_correlation_id
from pkg.messaging import check_rabbitmq_health, get_rabbitmq_client
from pkg.redis_client import check_redis_health

try:
    from .routes import router as task_router
except ImportError:
    from routes import router as task_router

settings = get_settings()
logger = get_logger("task-service")

REQUEST_COUNT = Counter(
    "task_http_requests_total",
    "Total HTTP Requests to Task Service",
    ["method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "task_http_request_duration_seconds",
    "HTTP Request Latency in Task Service",
    ["endpoint"]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting CloudTask Task Service...")
    try:
        await get_rabbitmq_client()
    except Exception as e:
        logger.warning(f"RabbitMQ connection failed on startup: {e}")
    yield
    logger.info("Shutting down CloudTask Task Service...")


app = FastAPI(
    title="CloudTask Task Management Service",
    version="1.0.0",
    description="Task lifecycle, CRUD, scheduling and dispatch microservice",
    lifespan=lifespan,
)


@app.middleware("http")
async def logging_and_metrics_middleware(request: Request, call_next):
    start_time = time.time()
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    set_correlation_id(correlation_id)

    response: Response = await call_next(request)

    duration = time.time() - start_time
    endpoint = request.url.path

    REQUEST_COUNT.labels(
        method=request.method,
        endpoint=endpoint,
        status=response.status_code
    ).inc()
    REQUEST_LATENCY.labels(endpoint=endpoint).observe(duration)

    response.headers["X-Correlation-ID"] = correlation_id
    return response


app.include_router(task_router)


@app.get("/health/live", tags=["Health"])
async def liveness():
    """Liveness probe: Checks if the container process is alive."""
    return {"status": "UP", "service": "task-service"}


@app.get("/health/ready", tags=["Health"])
async def readiness():
    """Readiness probe: Checks PostgreSQL, Redis, and RabbitMQ dependencies."""
    db_ok = await check_database_health()
    redis_ok = await check_redis_health()
    rmq_ok = await check_rabbitmq_health()

    if db_ok and redis_ok and rmq_ok:
        return {
            "status": "READY",
            "database": "CONNECTED",
            "redis": "CONNECTED",
            "rabbitmq": "CONNECTED"
        }
    return JSONResponse(
        status_code=503,
        content={
            "status": "NOT_READY",
            "database": "CONNECTED" if db_ok else "DISCONNECTED",
            "redis": "CONNECTED" if redis_ok else "DISCONNECTED",
            "rabbitmq": "CONNECTED" if rmq_ok else "DISCONNECTED"
        }
    )


@app.get("/metrics", tags=["Observability"])
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=settings.TASK_SERVICE_PORT, reload=False)
