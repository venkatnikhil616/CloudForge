import os
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from pkg.config import get_settings
from pkg.logger import get_logger, set_correlation_id
from pkg.redis_client import check_rate_limit, check_redis_health

settings = get_settings()
logger = get_logger("api-gateway")

# Upstream Service URLs (overridable for cloud platforms)
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", f"http://localhost:{settings.AUTH_SERVICE_PORT}")
TASK_SERVICE_URL = os.getenv("TASK_SERVICE_URL", f"http://localhost:{settings.TASK_SERVICE_PORT}")

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
    docs_url=None,
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


@app.get("/", response_class=HTMLResponse, tags=["General"])
async def root():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CloudTask Platform - Distributed Task Engine</title>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap">
  <style>
    :root {
      --bg: #0B0F19;
      --card-bg: #111827;
      --card-border: #1F2937;
      --primary: #3B82F6;
      --accent: #10B981;
      --text: #F3F4F6;
      --text-muted: #9CA3AF;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', sans-serif; }
    body { background-color: var(--bg); color: var(--text); padding: 40px 20px; line-height: 1.6; }
    .container { max-width: 960px; margin: 0 auto; }
    .header { text-align: center; margin-bottom: 40px; }
    .badge { display: inline-block; background: rgba(59, 130, 246, 0.15); color: #60A5FA; padding: 6px 14px; border-radius: 9999px; font-size: 0.85rem; font-weight: 600; margin-bottom: 12px; border: 1px solid rgba(59, 130, 246, 0.3); }
    h1 { font-size: 2.5rem; font-weight: 800; color: #FFFFFF; margin-bottom: 10px; }
    .subtitle { color: var(--text-muted); font-size: 1.15rem; max-width: 650px; margin: 0 auto; }
    .status-badge { display: inline-flex; align-items: center; gap: 8px; background: rgba(16, 185, 129, 0.15); color: #34D399; padding: 6px 16px; border-radius: 9999px; font-size: 0.9rem; font-weight: 600; border: 1px solid rgba(16, 185, 129, 0.3); margin-top: 20px; }
    .pulse { width: 10px; height: 10px; background-color: #10B981; border-radius: 50%; box-shadow: 0 0 10px #10B981; animation: pulse 2s infinite; }
    @keyframes pulse { 0% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(1.3); } 100% { opacity: 1; transform: scale(1); } }
    .actions { display: flex; justify-content: center; gap: 16px; margin: 35px 0; flex-wrap: wrap; }
    .btn { display: inline-flex; align-items: center; padding: 12px 24px; border-radius: 8px; font-weight: 600; text-decoration: none; transition: all 0.2s ease; font-size: 1rem; cursor: pointer; border: none; }
    .btn-primary { background: #2563EB; color: #FFFFFF; box-shadow: 0 4px 14px rgba(37, 99, 235, 0.35); }
    .btn-primary:hover { background: #1D4ED8; transform: translateY(-1px); }
    .btn-secondary { background: var(--card-bg); color: var(--text); border: 1px solid var(--card-border); }
    .btn-secondary:hover { background: #1F2937; transform: translateY(-1px); }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px; margin-top: 40px; }
    .card { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 12px; padding: 24px; }
    .card h3 { color: #FFFFFF; font-size: 1.15rem; margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
    .card p { color: var(--text-muted); font-size: 0.95rem; }
    .code-box { background: #000000; border: 1px solid var(--card-border); border-radius: 8px; padding: 16px; margin-top: 30px; font-family: monospace; font-size: 0.9rem; color: #A7F3D0; overflow-x: auto; }
    #health-panel { display: none; background: #0D1527; border: 1px solid #1D4ED8; border-radius: 10px; padding: 20px; margin: 25px auto 0; text-align: left; max-width: 600px; }
    #health-panel h4 { color: #60A5FA; margin-bottom: 10px; display: flex; align-items: center; justify-content: space-between; }
    #health-panel pre { background: #070B14; padding: 12px; border-radius: 6px; color: #34D399; font-size: 0.85rem; overflow-x: auto; margin-top: 10px; }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <span class="badge">Cloud-Native Distributed Systems</span>
      <h1>CloudTask Distributed Engine</h1>
      <p class="subtitle">A production-grade, asynchronous distributed task processing platform inspired by Celery, Sidekiq & AWS SQS.</p>
      <div class="status-badge">
        <div class="pulse"></div>
        <span>All Services Operational</span>
      </div>
    </div>

    <div class="actions">
      <a href="/docs" class="btn btn-primary">📖 Interactive Swagger UI (/docs)</a>
      <a href="/redoc" class="btn btn-secondary">📑 API ReDoc</a>
      <button onclick="runLiveHealthCheck()" class="btn btn-secondary" id="health-btn">💚 Live Health Check</button>
    </div>

    <div id="health-panel">
      <h4>
        <span>🟢 Live Diagnostics Result</span>
        <div style="display:flex;gap:10px;align-items:center;">
          <button type="button" onclick="copyHealthJson()" id="copy-btn" style="background:#1E293B;color:#94A3B8;border:1px solid #334155;border-radius:4px;padding:3px 8px;font-size:0.75rem;cursor:pointer;">📋 Copy JSON</button>
          <a href="/health/live" style="color:#60A5FA;font-size:0.8rem;text-decoration:underline;font-weight:500;">View /health/live</a>
        </div>
      </h4>
      <div id="health-content">Checking services...</div>
    </div>

    <div class="grid">
      <div class="card">
        <h3>🛡️ At-Least-Once Delivery</h3>
        <p>Reliable message execution with Redis distributed mutex locking & PostgreSQL idempotency keys.</p>
      </div>
      <div class="card">
        <h3>🔁 Exponential Retries & DLQ</h3>
        <p>Automated exponential backoff retries with dead-letter queueing for poisoned messages.</p>
      </div>
      <div class="card">
        <h3>⚡ Priority Task Queuing</h3>
        <p>RabbitMQ priority queues (1-10) with multi-worker concurrent dispatching.</p>
      </div>
      <div class="card">
        <h3>🔒 Stateless JWT Security</h3>
        <p>Bcrypt password hashing, token validation, rate limiting & correlation ID tracking.</p>
      </div>
    </div>

    <div class="code-box">
      # Test Platform via cURL:<br>
      curl https://cloudtask-platform.onrender.com/health/live<br>
      # Response: {"status": "UP", "service": "api-gateway"}
    </div>
  </div>

  <script>
    let lastHealthJson = '';

    function copyHealthJson() {
      if (lastHealthJson && navigator.clipboard) {
        navigator.clipboard.writeText(lastHealthJson);
        const btn = document.getElementById('copy-btn');
        btn.innerText = '✅ Copied!';
        setTimeout(() => { btn.innerText = '📋 Copy JSON'; }, 2000);
      }
    }

    async function runLiveHealthCheck() {
      const panel = document.getElementById('health-panel');
      const content = document.getElementById('health-content');
      const btn = document.getElementById('health-btn');
      
      panel.style.display = 'block';
      btn.innerText = '⏳ Testing...';
      content.innerHTML = '<span style="color: #FBBF24;">Pinging /health/live and /health/ready...</span>';
      
      try {
        const start = performance.now();
        const liveRes = await fetch('/health/live');
        const readyRes = await fetch('/health/ready');
        const latency = Math.round(performance.now() - start);
        
        const liveData = await liveRes.json();
        const readyData = await readyRes.json();
        
        lastHealthJson = JSON.stringify({ liveness: liveData, readiness: readyData, latency_ms: latency }, null, 2);
        btn.innerText = '✅ Health Verified (' + latency + 'ms)';
        content.innerHTML = `
          <p style="margin-bottom: 6px;"><strong>Status:</strong> <span style="color: #34D399;">ONLINE</span> (Latency: ${latency}ms)</p>
          <p style="margin-bottom: 6px;"><strong>API Gateway:</strong> ${liveData.status === 'UP' ? '✅ UP' : '❌ DOWN'}</p>
          <p style="margin-bottom: 6px;"><strong>Redis Cache:</strong> ${readyData.redis === 'CONNECTED' ? '✅ CONNECTED' : '⚠️ ' + readyData.redis}</p>
          <pre>${lastHealthJson}</pre>
        `;
      } catch (err) {
        btn.innerText = '❌ Test Error';
        content.innerHTML = '<span style="color: #EF4444;">Error testing health: ' + err.message + '</span>';
      }
    }
  </script>
</body>
</html>"""


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    response = get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=app.title + " - Swagger UI",
        swagger_favicon_url="https://fastapi.tiangolo.com/img/favicon.png",
    )
    html = response.body.decode("utf-8")
    top_bar = """
    <div style="background:#0B0F19;border-bottom:1px solid #1F2937;padding:12px 24px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:99999;font-family:system-ui, -apple-system, sans-serif;">
      <div style="display:flex;align-items:center;gap:12px;">
        <span style="font-weight:700;color:#FFFFFF;font-size:1.1rem;letter-spacing:-0.02em;">⚡ CloudTask API Explorer</span>
        <span style="background:rgba(59,130,246,0.15);color:#60A5FA;padding:3px 10px;border-radius:9999px;font-size:0.75rem;font-weight:600;border:1px solid rgba(59,130,246,0.3);">Swagger UI</span>
      </div>
      <a href="/" style="background:#2563EB;color:#FFFFFF;text-decoration:none;padding:8px 18px;border-radius:6px;font-weight:600;font-size:0.9rem;display:inline-flex;align-items:center;gap:6px;box-shadow:0 2px 8px rgba(37,99,235,0.4);transition:background 0.2s;">
        ← Back to Portal
      </a>
    </div>
    """
    html = html.replace("<body>", f"<body>{top_bar}", 1)
    return HTMLResponse(content=html)


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
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Service {service_name} unavailable") from exc

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
    uvicorn.run(app, host="0.0.0.0", port=settings.API_GATEWAY_PORT, reload=False)
