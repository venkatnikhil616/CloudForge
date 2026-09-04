import time
import uuid
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, HTTPException, status
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

from pkg.config import get_settings
from pkg.logger import get_logger, set_correlation_id
from pkg.redis_client import check_rate_limit, check_redis_health
from pkg.security import decode_access_token

settings = get_settings()
logger = get_logger("api-gateway")

# Upstream Service URLs
AUTH_SERVICE_URL = f"http://localhost:{settings.AUTH_SERVICE_PORT}"
TASK_SERVICE_URL = f"http://localhost:{settings.TASK_SERVICE_PORT}"

# Metrics
GATEWAY_REQUESTS = Counter(
    "gateway_requests_total",
    "Total requests routed through API Gateway",
    ["method", "service", "status"]
)
GATEWAY_LATENCY = Histogram(
    "gateway_request_duration_seconds",
    "API Gateway request latency",
    ["service"]
)

http_client: httpx.AsyncClient = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    logger.info("Initializing API Gateway reverse proxy client...")
    http_client = httpx.AsyncClient(timeout=30.0)
    yield
    if http_client:
        await http_client.aclose()
    logger.info("API Gateway shut down.")


app = FastAPI(
    title="CloudTask API Gateway",
    version="1.0.0",
    description="Unified API Gateway with rate limiting, correlation tracking, and security enforcement",
    lifespan=lifespan,
)


@app.middleware("http")
async def gateway_middleware(request: Request, call_next):
    start_time = time.time()
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    set_correlation_id(correlation_id)

    # Rate Limiting check (Section 5.1 in document)
    client_ip = request.client.host if request.client else "unknown"
    is_allowed = await check_rate_limit(f"ip:{client_ip}", limit=200, window_seconds=60)
    if not is_allowed:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "Rate limit exceeded. Please try again later."},
            headers={"Retry-After": "60", "X-Correlation-ID": correlation_id}
        )

    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    response.headers["X-Response-Time-Ms"] = str(int((time.time() - start_time) * 1000))
    return response


# Health and Metrics
@app.get("/health/live", tags=["Health"])
async def liveness():
    return {"status": "UP", "service": "api-gateway"}


@app.get("/health/ready", tags=["Health"])
async def readiness():
    redis_ok = await check_redis_health()
    if redis_ok:
        return {"status": "READY", "redis": "CONNECTED"}
    return JSONResponse(status_code=503, content={"status": "NOT_READY", "redis": "DISCONNECTED"})


@app.get("/metrics", tags=["Observability"])
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# Proxy helper
async def proxy_request(service_name: str, target_base_url: str, request: Request, path: str):
    start_time = time.time()
    url = f"{target_base_url}/{path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers = dict(request.headers)
    headers.pop("host", None)
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    headers["X-Correlation-ID"] = correlation_id

    body = await request.body()

    try:
        upstream_resp = await http_client.request(
            method=request.method,
            url=url,
            headers=headers,
            content=body,
        )
    except httpx.RequestError as exc:
        logger.error(f"Error communicating with {service_name}: {exc}")
        GATEWAY_REQUESTS.labels(method=request.method, service=service_name, status=503).inc()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Service {service_name} unavailable")

    duration = time.time() - start_time
    GATEWAY_REQUESTS.labels(method=request.method, service=service_name, status=upstream_resp.status_code).inc()
    GATEWAY_LATENCY.labels(service=service_name).observe(duration)

    return Response(
        content=upstream_resp.content,
        status_code=upstream_resp.status_code,
        headers=dict(upstream_resp.headers),
        media_type=upstream_resp.headers.get("content-type"),
    )


# Auth Service Proxy Routes (/api/v1/auth/...)
@app.api_route("/api/v1/auth/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def auth_proxy(path: str, request: Request):
    return await proxy_request("auth-service", f"{AUTH_SERVICE_URL}/auth", request, path)


# Task Service Proxy Routes (/api/v1/tasks/...)
@app.api_route("/api/v1/tasks/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def task_proxy(path: str, request: Request):
    return await proxy_request("task-service", f"{TASK_SERVICE_URL}/tasks", request, path)


@app.api_route("/api/v1/tasks", methods=["GET", "POST"])
async def task_root_proxy(request: Request):
    return await proxy_request("task-service", f"{TASK_SERVICE_URL}/tasks", request, "")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=settings.API_GATEWAY_PORT, reload=False)
