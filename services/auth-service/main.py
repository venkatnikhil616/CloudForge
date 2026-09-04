import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from pkg.config import get_settings
from pkg.database import check_database_health
from pkg.logger import get_logger, set_correlation_id

try:
    from .routes import router as auth_router
except ImportError:
    from routes import router as auth_router

settings = get_settings()
logger = get_logger("auth-service")

# Prometheus Metrics
REQUEST_COUNT = Counter(
    "auth_http_requests_total",
    "Total HTTP Requests to Auth Service",
    ["method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "auth_http_request_duration_seconds",
    "HTTP Request Latency in Auth Service",
    ["endpoint"]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting CloudTask Auth Service...")
    yield
    logger.info("Shutting down CloudTask Auth Service...")


app = FastAPI(
    title="CloudTask Auth Service",
    version="1.0.0",
    description="Authentication and Identity microservice for CloudTask platform",
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


# Include Authentication routes
app.include_router(auth_router)


# Kubernetes health and readiness probes
@app.get("/health/live", tags=["Health"])
async def liveness():
    """Liveness probe: Checks if the container process is alive."""
    return {"status": "UP", "service": "auth-service"}


@app.get("/health/ready", tags=["Health"])
async def readiness():
    """Readiness probe: Checks if database dependencies are ready."""
    db_ok = await check_database_health()
    if db_ok:
        return {"status": "READY", "database": "CONNECTED"}
    return JSONResponse(
        status_code=503,
        content={"status": "NOT_READY", "database": "DISCONNECTED"}
    )


@app.get("/metrics", tags=["Observability"])
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.AUTH_SERVICE_PORT, reload=False)
