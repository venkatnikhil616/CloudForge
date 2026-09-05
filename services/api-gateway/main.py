import asyncio
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

# Ensure monorepo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from pkg.config import get_settings
from pkg.logger import get_logger, set_correlation_id
from pkg.redis_client import check_rate_limit, check_redis_health

settings = get_settings()
logger = get_logger("api-gateway")

# Upstream Service URLs (overridable for cloud platforms)
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", f"http://127.0.0.1:{settings.AUTH_SERVICE_PORT}")
TASK_SERVICE_URL = os.getenv("TASK_SERVICE_URL", f"http://127.0.0.1:{settings.TASK_SERVICE_PORT}")

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
load_errors: dict = {}
auth_loaded: bool = False
task_loaded: bool = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    logger.info("Initializing API Gateway client...")
    http_client = httpx.AsyncClient(timeout=30.0)

    # Automatically initialize database schema and seed default admin user on startup
    try:
        from sqlalchemy import select

        from pkg.database import AsyncSessionLocal, Base, engine
        from pkg.models import User
        from pkg.security import hash_password

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with AsyncSessionLocal() as session:
            stmt = select(User).where(User.email == "admin@cloudtask.dev")
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if not existing:
                admin_user = User(
                    id=str(uuid.uuid4()),
                    email="admin@cloudtask.dev",
                    hashed_password=hash_password("AdminSecurePass123!"),
                    full_name="CloudTask Admin",
                    role="admin",
                    is_active=True,
                )
                session.add(admin_user)
                await session.commit()
                logger.info("Auto-initialized database and seeded admin user.")
    except Exception as e:
        logger.warning(f"Database auto-setup: {e}")

    worker_task = None

    async def gateway_worker_loop():
        try:
            from services.worker.main import poll_and_execute_tasks
            while True:
                await asyncio.sleep(2)
                await poll_and_execute_tasks()
        except asyncio.CancelledError:
            pass
        except Exception as err:
            logger.warning(f"Gateway worker background loop: {err}")

    worker_task = asyncio.create_task(gateway_worker_loop())

    yield
    if worker_task:
        worker_task.cancel()
    if http_client:
        await http_client.aclose()
    logger.info("API Gateway shut down.")


app = FastAPI(
    title="CloudTask API Gateway",
    version="1.0.0",
    description="Unified API Gateway with rate limiting, correlation tracking, and security enforcement",
    lifespan=lifespan,
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def gateway_middleware(request: Request, call_next):
    start_time = time.time()
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    set_correlation_id(correlation_id)

    # Rate limiting check per client IP (sliding-window Redis limiter)
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
    if request.url.path in ("/", "/dashboard", "/docs") or "text/html" in response.headers.get("content-type", ""):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/", response_class=HTMLResponse, tags=["General"])
async def root():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0">
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
    body { background-color: var(--bg); color: var(--text); padding: 40px 20px; line-height: 1.6; min-height: 100vh; }
    .container { max-width: 960px; margin: 0 auto; width: 100%; }
    .header { text-align: center; margin-bottom: 40px; }
    .badge { display: inline-block; background: rgba(59, 130, 246, 0.15); color: #60A5FA; padding: 6px 14px; border-radius: 9999px; font-size: 0.85rem; font-weight: 600; margin-bottom: 12px; border: 1px solid rgba(59, 130, 246, 0.3); }
    h1 { font-size: 2.5rem; font-weight: 800; color: #FFFFFF; margin-bottom: 10px; letter-spacing: -0.02em; }
    .subtitle { color: var(--text-muted); font-size: 1.15rem; max-width: 650px; margin: 0 auto; }
    .status-badge { display: inline-flex; align-items: center; gap: 8px; background: rgba(16, 185, 129, 0.15); color: #34D399; padding: 6px 16px; border-radius: 9999px; font-size: 0.9rem; font-weight: 600; border: 1px solid rgba(16, 185, 129, 0.3); margin-top: 20px; }
    .pulse { width: 10px; height: 10px; background-color: #10B981; border-radius: 50%; box-shadow: 0 0 10px #10B981; animation: pulse 2s infinite; }
    @keyframes pulse { 0% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(1.3); } 100% { opacity: 1; transform: scale(1); } }
    .actions { display: flex; justify-content: center; gap: 16px; margin: 35px 0; flex-wrap: wrap; }
    .btn { display: inline-flex; align-items: center; justify-content: center; padding: 12px 24px; border-radius: 8px; font-weight: 600; text-decoration: none; transition: all 0.2s ease; font-size: 1rem; cursor: pointer; border: none; min-height: 48px; touch-action: manipulation; }
    .btn-primary { background: #2563EB; color: #FFFFFF; box-shadow: 0 4px 14px rgba(37, 99, 235, 0.35); }
    .btn-primary:hover { background: #1D4ED8; transform: translateY(-1px); }
    .btn-secondary { background: var(--card-bg); color: var(--text); border: 1px solid var(--card-border); }
    .btn-secondary:hover { background: #1F2937; transform: translateY(-1px); }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 20px; margin-top: 40px; }
    .card { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 12px; padding: 24px; }
    .card h3 { color: #FFFFFF; font-size: 1.15rem; margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
    .card p { color: var(--text-muted); font-size: 0.95rem; }
    .code-box { background: #000000; border: 1px solid var(--card-border); border-radius: 8px; padding: 16px; margin-top: 30px; font-family: monospace; font-size: 0.9rem; color: #A7F3D0; overflow-x: auto; word-break: break-all; }
    #health-panel { display: none; background: #0D1527; border: 1px solid #1D4ED8; border-radius: 10px; padding: 20px; margin: 25px auto 0; text-align: left; max-width: 600px; width: 100%; }
    #health-panel h4 { color: #60A5FA; margin-bottom: 10px; display: flex; align-items: center; justify-content: space-between; }
    #health-panel pre { background: #070B14; padding: 12px; border-radius: 6px; color: #34D399; font-size: 0.85rem; overflow-x: auto; margin-top: 10px; }

    @media (max-width: 640px) {
      body { padding: 24px 14px; }
      h1 { font-size: 1.85rem; }
      .subtitle { font-size: 0.95rem; }
      .actions { flex-direction: column; width: 100%; gap: 10px; }
      .btn { width: 100%; font-size: 0.95rem; }
      .grid { grid-template-columns: 1fr; gap: 14px; margin-top: 24px; }
      .card { padding: 18px; }
      #health-panel { padding: 14px; }
      .badge { font-size: 0.75rem; padding: 4px 10px; }
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <span class="badge">Distributed Task Engine</span>
      <h1>CloudTask Platform</h1>
      <p class="subtitle">High-throughput asynchronous task orchestration, resilient worker execution, and real-time observability.</p>
      <div class="status-badge">
        <div class="pulse"></div>
        <span>All Services Operational</span>
      </div>
    </div>

    <div class="actions">
      <a href="/dashboard" class="btn btn-primary" style="background:#10B981;box-shadow:0 4px 14px rgba(16,185,129,0.35);">Real-Time Task Dashboard</a>
      <a href="/docs" class="btn btn-primary">Swagger UI (/docs)</a>
      <button onclick="runLiveHealthCheck()" class="btn btn-secondary" id="health-btn">Live Diagnostics Check</button>
    </div>

    <div id="health-panel">
      <h4>
        <span>Live Diagnostics Result</span>
        <button type="button" onclick="copyHealthJson()" id="copy-btn" style="background:#1E293B;color:#94A3B8;border:1px solid #334155;border-radius:4px;padding:4px 10px;font-size:0.75rem;cursor:pointer;font-weight:500;">Copy JSON</button>
      </h4>
      <div id="health-content">Checking services...</div>
    </div>

    <div class="grid">
      <div class="card">
        <h3>At-Least-Once Delivery</h3>
        <p>Reliable message execution with Redis distributed mutex locking & PostgreSQL idempotency keys.</p>
      </div>
      <div class="card">
        <h3>Exponential Retries & DLQ</h3>
        <p>Automated exponential backoff retries with dead-letter queueing for poisoned messages.</p>
      </div>
      <div class="card">
        <h3>Priority Task Queuing</h3>
        <p>RabbitMQ priority queues (1-10) with multi-worker concurrent dispatching.</p>
      </div>
      <div class="card">
        <h3>Stateless JWT Security</h3>
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
        btn.innerText = 'Copied!';
        setTimeout(() => { btn.innerText = 'Copy JSON'; }, 2000);
      }
    }

    async function runLiveHealthCheck() {
      const panel = document.getElementById('health-panel');
      const content = document.getElementById('health-content');
      const btn = document.getElementById('health-btn');
      
      panel.style.display = 'block';
      btn.innerText = 'Testing...';
      content.innerHTML = '<span style="color: #FBBF24;">Pinging /health/live and /health/ready...</span>';
      
      try {
        const start = performance.now();
        const liveRes = await fetch('/health/live');
        const readyRes = await fetch('/health/ready');
        const latency = Math.round(performance.now() - start);
        
        const liveData = await liveRes.json();
        const readyData = await readyRes.json();
        
        lastHealthJson = JSON.stringify({ liveness: liveData, readiness: readyData, latency_ms: latency }, null, 2);
        btn.innerText = 'Health Verified (' + latency + 'ms)';
        content.innerHTML = `
          <p style="margin-bottom: 6px;"><strong>Status:</strong> <span style="color: #34D399;">ONLINE</span> (Latency: ${latency}ms)</p>
          <p style="margin-bottom: 6px;"><strong>API Gateway:</strong> ${liveData.status === 'UP' ? 'UP' : 'DOWN'}</p>
          <p style="margin-bottom: 6px;"><strong>Redis Cache:</strong> ${readyData.redis === 'CONNECTED' ? 'CONNECTED' : readyData.redis}</p>
          <pre>${lastHealthJson}</pre>
        `;
      } catch (err) {
        btn.innerText = 'Test Error';
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
    <div style="background:#0B0F19;border-bottom:1px solid #1F2937;padding:10px 16px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:99999;font-family:system-ui, -apple-system, sans-serif;flex-wrap:wrap;gap:8px;">
      <div style="display:flex;align-items:center;gap:8px;">
        <span style="font-weight:700;color:#FFFFFF;font-size:0.95rem;letter-spacing:-0.02em;">CloudTask API Explorer</span>
        <span style="background:rgba(59,130,246,0.15);color:#60A5FA;padding:2px 8px;border-radius:9999px;font-size:0.7rem;font-weight:600;border:1px solid rgba(59,130,246,0.3);">Swagger UI</span>
      </div>
      <a href="/" style="background:#2563EB;color:#FFFFFF;text-decoration:none;padding:6px 14px;border-radius:6px;font-weight:600;font-size:0.85rem;display:inline-flex;align-items:center;gap:6px;box-shadow:0 2px 8px rgba(37,99,235,0.4);transition:background 0.2s;">
        ← Back to Portal
      </a>
    </div>
    """
    html = html.replace("<body>", f"<body>{top_bar}", 1)
    return HTMLResponse(content=html)


@app.get("/dashboard", response_class=HTMLResponse, tags=["Dashboard"])
async def real_time_dashboard():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0">
  <title>CloudTask - Enterprise Distributed Task Dashboard</title>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap">
  <style>
    :root {
      --bg: #080C15;
      --card-bg: #0F172A;
      --card-inner: #0B1120;
      --card-border: #1E293B;
      --card-border-light: #334155;
      --primary: #2563EB;
      --primary-hover: #1D4ED8;
      --success: #10B981;
      --warning: #F59E0B;
      --danger: #EF4444;
      --text: #F8FAFC;
      --text-muted: #94A3B8;
      --radius: 10px;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }
    body { background-color: var(--bg); color: var(--text); padding: 16px 12px; min-height: 100vh; line-height: 1.5; -webkit-font-smoothing: antialiased; }
    .container { max-width: 1400px; margin: 0 auto; width: 100%; }

    /* Top Navigation */
    .nav { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--card-border); padding-bottom: 14px; margin-bottom: 16px; flex-wrap: wrap; gap: 10px; }
    .nav-brand { display: flex; align-items: center; gap: 8px; font-weight: 800; font-size: 1.15rem; color: #FFFFFF; }
    .nav-links { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .btn { padding: 8px 14px; border-radius: 6px; font-size: 0.85rem; font-weight: 600; text-decoration: none; border: none; cursor: pointer; transition: all 0.2s; display: inline-flex; align-items: center; gap: 6px; touch-action: manipulation; }
    .btn:active { transform: scale(0.97); }
    .btn-primary { background: var(--primary); color: #fff; }
    .btn-primary:hover { background: var(--primary-hover); }
    .btn-success { background: var(--success); color: #fff; }
    .btn-danger { background: var(--danger); color: #fff; }
    .btn-secondary { background: var(--card-bg); color: var(--text); border: 1px solid var(--card-border); }
    .btn-secondary:hover { background: #1E293B; }

    /* Cluster Status Banner */
    .cluster-banner { background: rgba(37, 99, 235, 0.08); border: 1px solid rgba(37, 99, 235, 0.25); border-radius: 8px; padding: 8px 14px; margin-bottom: 18px; display: flex; justify-content: space-between; align-items: center; font-size: 0.8rem; color: #93C5FD; flex-wrap: wrap; gap: 8px; }
    .status-pill { display: inline-flex; align-items: center; gap: 6px; background: rgba(16, 185, 129, 0.18); color: #34D399; padding: 3px 10px; border-radius: 9999px; font-weight: 600; font-size: 0.75rem; }
    .pulse-dot { width: 8px; height: 8px; background: #10B981; border-radius: 50%; box-shadow: 0 0 8px #10B981; animation: pulse 2s infinite; }
    @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.4; } 100% { opacity: 1; } }

    /* Metrics Bar */
    .metrics-bar { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 20px; }
    .metric-card { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: var(--radius); padding: 14px 16px; transition: transform 0.2s; }
    .metric-label { font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; font-weight: 700; letter-spacing: 0.03em; }
    .metric-val { font-size: 1.7rem; font-weight: 800; margin-top: 4px; line-height: 1.1; }

    /* Filter & Search Toolbar */
    .filter-toolbar { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: var(--radius); padding: 12px 14px; margin-bottom: 18px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
    .search-box { display: flex; align-items: center; gap: 8px; background: var(--card-inner); border: 1px solid var(--card-border); border-radius: 6px; padding: 6px 12px; flex: 1; min-width: 220px; }
    .search-box input { background: transparent; border: none; outline: none; color: #fff; font-size: 0.85rem; width: 100%; }
    .filter-selects { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .filter-select { background: var(--card-inner); border: 1px solid var(--card-border); border-radius: 6px; padding: 7px 10px; color: #fff; font-size: 0.82rem; outline: none; cursor: pointer; }

    /* Quick Dispatcher Form */
    .dispatch-box { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: var(--radius); padding: 18px; margin-bottom: 20px; transition: all 0.3s ease; }
    .dispatch-box h3 { font-size: 1rem; margin-bottom: 14px; display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
    .form-group { display: flex; flex-direction: column; gap: 5px; }
    .form-group label { font-size: 0.78rem; color: var(--text-muted); font-weight: 600; }
    .form-control { background: var(--card-inner); border: 1px solid var(--card-border); border-radius: 6px; padding: 8px 10px; color: #fff; font-size: 0.85rem; outline: none; }
    .form-control:focus { border-color: var(--primary); }

    /* Mobile Segmented Control Tabs */
    .mobile-tabs { display: none; margin-bottom: 14px; overflow-x: auto; -webkit-overflow-scrolling: touch; padding-bottom: 4px; gap: 6px; }
    .tab-pill { padding: 6px 12px; border-radius: 9999px; font-size: 0.8rem; font-weight: 600; border: 1px solid var(--card-border); background: var(--card-bg); color: var(--text-muted); cursor: pointer; white-space: nowrap; flex-shrink: 0; }
    .tab-pill.active { background: #2563EB; color: #fff; border-color: #3B82F6; }

    /* Kanban Grid */
    .kanban-board-wrapper { width: 100%; overflow-x: auto; padding-bottom: 12px; }
    .kanban-grid { display: grid; grid-template-columns: repeat(4, minmax(260px, 1fr)); gap: 14px; align-items: stretch; min-width: 1080px; }
    .kanban-col { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: var(--radius); padding: 14px; display: flex; flex-direction: column; height: 640px; max-height: 72vh; box-sizing: border-box; }
    .col-header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 10px; border-bottom: 1px solid var(--card-border); margin-bottom: 12px; font-weight: 700; font-size: 0.85rem; letter-spacing: 0.02em; flex-shrink: 0; }
    .kanban-tasks { flex: 1; overflow-y: auto; overflow-x: hidden; padding-right: 4px; display: flex; flex-direction: column; gap: 10px; min-height: 0; }
    .kanban-tasks::-webkit-scrollbar { width: 6px; }
    .kanban-tasks::-webkit-scrollbar-track { background: rgba(15, 23, 42, 0.4); border-radius: 4px; }
    .kanban-tasks::-webkit-scrollbar-thumb { background: #334155; border-radius: 4px; }
    .kanban-tasks::-webkit-scrollbar-thumb:hover { background: #475569; }

    /* Task Card */
    .task-card { background: var(--card-inner); border: 1px solid var(--card-border); border-radius: 8px; padding: 12px; margin-bottom: 10px; transition: all 0.2s; cursor: pointer; flex-shrink: 0; }
    .task-card:hover { border-color: #3B82F6; transform: translateY(-1px); }
    .task-title { font-weight: 600; font-size: 0.88rem; color: #FFFFFF; margin-bottom: 6px; word-break: break-word; }
    .task-meta { font-size: 0.72rem; color: var(--text-muted); display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
    .tag { background: #1E293B; padding: 2px 6px; border-radius: 4px; font-weight: 600; font-size: 0.7rem; }
    .tag-prio { background: rgba(245, 158, 11, 0.2); color: #FBBF24; }
    .tag-delay { background: rgba(139, 92, 246, 0.2); color: #A78BFA; }
    .tag-wh { background: rgba(16, 185, 129, 0.2); color: #34D399; }
    .progress-bar { width: 100%; height: 5px; background: #1F2937; border-radius: 9999px; overflow: hidden; margin: 6px 0; }
    .progress-fill { height: 100%; background: linear-gradient(90deg, #3B82F6, #10B981); transition: width 0.4s; }
    .card-actions { display: flex; gap: 6px; margin-top: 8px; }
    .btn-xs { padding: 4px 8px; font-size: 0.72rem; border-radius: 4px; }

    /* Modal Component (Slide-over/Bottom-sheet on mobile, centered on desktop) */
    .modal-backdrop { display: none; position: fixed; inset: 0; background: rgba(0, 0, 0, 0.75); backdrop-filter: blur(4px); z-index: 99999; justify-content: center; align-items: center; padding: 12px; }
    .modal-dialog { background: #0F172A; border: 1px solid #334155; border-radius: 12px; width: 100%; max-width: 720px; max-height: 90vh; display: flex; flex-direction: column; overflow: hidden; box-shadow: 0 20px 40px rgba(0,0,0,0.6); animation: modalIn 0.2s ease-out; }
    @keyframes modalIn { from { opacity: 0; transform: scale(0.96); } to { opacity: 1; transform: scale(1); } }
    .modal-header { padding: 14px 18px; border-bottom: 1px solid #1E293B; display: flex; justify-content: space-between; align-items: center; }
    .modal-header h3 { font-size: 1.05rem; font-weight: 700; color: #fff; word-break: break-all; }
    .modal-close { background: transparent; border: none; color: #94A3B8; font-size: 1.3rem; cursor: pointer; padding: 4px 8px; }
    .modal-body { padding: 16px 18px; overflow-y: auto; display: flex; flex-direction: column; gap: 14px; font-size: 0.85rem; }
    .modal-footer { padding: 12px 18px; border-top: 1px solid #1E293B; display: flex; justify-content: flex-end; gap: 10px; background: #0B1120; }
    .detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; }
    .detail-item { background: #0B1120; border: 1px solid #1E293B; border-radius: 6px; padding: 8px 12px; }
    .detail-label { font-size: 0.7rem; color: #94A3B8; text-transform: uppercase; font-weight: 700; margin-bottom: 2px; }
    .detail-val { font-size: 0.85rem; color: #F1F5F9; word-break: break-all; font-family: monospace; display: flex; align-items: center; justify-content: space-between; }
    .code-block { background: #050811; border: 1px solid #1E293B; border-radius: 6px; padding: 10px; font-family: monospace; font-size: 0.78rem; color: #A7F3D0; overflow-x: auto; max-height: 180px; }
    .attempt-table { width: 100%; border-collapse: collapse; font-size: 0.78rem; margin-top: 6px; }
    .attempt-table th, .attempt-table td { padding: 6px 10px; text-align: left; border-bottom: 1px solid #1E293B; }
    .attempt-table th { color: #94A3B8; text-transform: uppercase; font-size: 0.7rem; }

    /* Media Queries for Mobile and Tablet */
    @media (max-width: 820px) {
      body { padding: 10px 8px; }
      .container { max-width: 100%; }
      .metrics-bar { grid-template-columns: repeat(2, 1fr); }
      .mobile-tabs { display: flex; }
      .kanban-board-wrapper { overflow-x: visible; }
      .kanban-grid { grid-template-columns: 1fr; min-width: 0; }
      .kanban-col { height: auto; max-height: none; min-height: 250px; }
      .kanban-tasks { max-height: 480px; }
      .kanban-col.mobile-hidden { display: none !important; }
      .filter-toolbar { flex-direction: column; align-items: stretch; }
      .search-box { width: 100%; }
      .filter-selects { width: 100%; justify-content: space-between; }
      .filter-select { flex: 1; }
      .modal-backdrop { align-items: flex-end; padding: 0; }
      .modal-dialog { border-radius: 16px 16px 0 0; max-height: 85vh; }
      .nav-links .btn { padding: 6px 10px; font-size: 0.78rem; }
    }

    @media (max-width: 520px) {
      .metrics-bar { grid-template-columns: 1fr 1fr; gap: 8px; }
      .metric-card { padding: 10px 12px; }
      .metric-val { font-size: 1.4rem; }
      .form-grid { grid-template-columns: 1fr; }
    }

    /* Toast Notification System */
    #toast-container { position: fixed; top: 20px; right: 20px; z-index: 1000000; display: flex; flex-direction: column; gap: 10px; max-width: 420px; width: calc(100% - 40px); pointer-events: none; }
    .toast-msg { background: #0F172A; border: 1px solid #334155; border-radius: 8px; padding: 12px 16px; box-shadow: 0 10px 30px rgba(0,0,0,0.7); pointer-events: auto; animation: toastSlideIn 0.25s cubic-bezier(0.16, 1, 0.3, 1); display: flex; flex-direction: column; gap: 6px; font-size: 0.85rem; color: #F8FAFC; transition: opacity 0.3s, transform 0.3s; }
    @keyframes toastSlideIn { from { opacity: 0; transform: translateY(-12px); } to { opacity: 1; transform: translateY(0); } }
    .toast-msg.toast-success { border-left: 4px solid #10B981; }
    .toast-msg.toast-info { border-left: 4px solid #3B82F6; }
    .toast-msg.toast-warning { border-left: 4px solid #F59E0B; }
    .toast-msg.toast-error { border-left: 4px solid #EF4444; }
    .toast-header { display: flex; justify-content: space-between; align-items: center; font-weight: 700; }
    .toast-title { font-size: 0.9rem; color: #FFFFFF; font-weight: 700; }
    .toast-body { color: #94A3B8; font-size: 0.8rem; line-height: 1.4; word-break: break-word; }
    .toast-actions { display: flex; gap: 8px; margin-top: 4px; flex-wrap: wrap; }
    .toast-close { background: none; border: none; color: #64748B; cursor: pointer; font-size: 1.2rem; line-height: 1; padding: 0 4px; }
    .toast-close:hover { color: #CBD5E1; }

    /* Dispatch Box Highlight Pulse */
    @keyframes pulseHighlight {
      0% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.6); border-color: #10B981; }
      50% { box-shadow: 0 0 20px 4px rgba(16, 185, 129, 0.4); border-color: #34D399; }
      100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
    }
    .pulse-box-highlight { animation: pulseHighlight 1.8s ease-out; }
  </style>
</head>
<body>
  <!-- Toast Notification Action Center -->
  <div id="toast-container"></div>

  <div class="container">
    <!-- Server Offline / Static Server Notice -->
    <div id="offline-alert" style="display:none; background:rgba(239,68,68,0.15); border:1px solid rgba(239,68,68,0.4); border-radius:8px; padding:12px 16px; margin-bottom:16px; color:#FCA5A5; font-size:0.85rem; line-height:1.5;">
      <strong>Backend Offline / Static Server Notice:</strong> The dashboard cannot communicate with the FastAPI backend. If you are serving files using <code>python -m http.server</code>, API operations (task enqueueing, queue processing, CSV audit streaming) will not run. Please execute <code>./run_local.sh</code> or <code>make run</code> in your terminal to start the live engine with full API and worker support.
    </div>

    <!-- Navbar -->
    <div class="nav">
      <div class="nav-brand">
        <span>CloudTask Engine</span>
        <div class="status-pill">
          <div class="pulse-dot"></div>
          <span>Active</span>
        </div>
      </div>
      <div class="nav-links">
        <a href="/" class="btn btn-secondary">← Portal</a>
        <a href="/docs" class="btn btn-secondary">API Docs</a>
        <button onclick="toggleExecutionMode()" class="btn btn-secondary" id="mode-toggle-btn" title="Toggle between manual batch staging and instant auto-dispatch" style="border: 1px solid #10B981; color: #6EE7B7;">Mode: <span id="mode-badge" style="font-weight:700; color:#34D399;">Auto-Start</span></button>
        <button onclick="startPriorityProcessing()" class="btn btn-success" id="start-btn" style="background:#10B981; box-shadow:0 0 14px rgba(16,185,129,0.4); font-weight:700; padding:8px 16px;">Start Processing (<span id="start-count">0</span>)</button>
        <button onclick="exportTasksCsv()" class="btn btn-secondary" id="export-btn">Export CSV</button>
        <button onclick="toggleDispatcher()" class="btn btn-primary" id="toggle-dispatch-btn">New Task</button>
        <button onclick="fetchTasks()" class="btn btn-secondary" id="refresh-btn">Refresh</button>
      </div>
    </div>

    <!-- Cluster Fleet Summary Banner -->
    <div class="cluster-banner" id="status-banner">
      <div id="mode-guidance-text"><strong>Auto-Start Mode:</strong> Tasks execute automatically in background as soon as they are submitted.</div>
      <div id="filter-count-badge" style="font-weight:600;">Showing 0 tasks</div>
    </div>

    <!-- Metrics Bar -->
    <div class="metrics-bar">
      <div class="metric-card">
        <div class="metric-label">Total Tasks</div>
        <div class="metric-val" id="total-count" style="color: #60A5FA;">0</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Queued / Ready</div>
        <div class="metric-val" id="queued-count" style="color: #FBBF24;">0</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Running in Fleet</div>
        <div class="metric-val" id="running-count" style="color: #38BDF8;">0</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Successfully Finished</div>
        <div class="metric-val" id="success-count" style="color: #34D399;">0</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Dead Letter Queue</div>
        <div class="metric-val" id="dlq-count" style="color: #F87171;">0</div>
      </div>
    </div>

    <!-- Filter & Search Toolbar -->
    <div class="filter-toolbar">
      <div class="search-box">
        <input type="text" id="task-search" placeholder="Search tasks by title, ID, or trace..." oninput="handleSearch(this.value)" />
      </div>
      <div class="filter-selects">
        <select id="filter-type" class="filter-select" onchange="applyFilters()">
          <option value="">All Task Types</option>
          <option value="report_generation">report_generation</option>
          <option value="data_processing">data_processing</option>
          <option value="email_dispatch">email_dispatch</option>
          <option value="system_cleanup">system_cleanup</option>
        </select>
        <select id="filter-priority" class="filter-select" onchange="applyFilters()">
          <option value="">All Priorities</option>
          <option value="10">Priority 10 (Critical)</option>
          <option value="8">Priority 8 (High)</option>
          <option value="5">Priority 5 (Normal)</option>
          <option value="1">Priority 1 (Low)</option>
        </select>
        <select id="filter-duplicate" class="filter-select" onchange="applyFilters()">
          <option value="">All Tasks</option>
          <option value="duplicates">Duplicates Only</option>
        </select>
      </div>
    </div>

    <!-- Quick Dispatcher Form -->
    <div class="dispatch-box" id="dispatch-panel" style="display: block;">
      <h3>
        <span>Dispatch Asynchronous Task to Cluster</span>
      </h3>
      <form id="task-form" onsubmit="handleDispatch(event); return false;">
        <div class="form-grid">
          <div class="form-group">
            <div style="display:flex; justify-content:space-between; align-items:center;">
              <label>Task Title</label>
              <span id="duplicate-warning" style="display:none; font-size:0.72rem; color:#F59E0B; font-weight:600;">Duplicate Detected</span>
            </div>
            <input type="text" id="task-title" class="form-control" placeholder="e.g. Ingest Customer Ledger" oninput="checkDuplicateInline()" />
          </div>
          <div class="form-group">
            <label>Handler Type</label>
            <select id="task-type" class="form-control" onchange="checkDuplicateInline()">
              <option value="report_generation">report_generation (PDF Export)</option>
              <option value="data_processing">data_processing (ETL Batch)</option>
              <option value="email_dispatch">email_dispatch (Transactional Email)</option>
              <option value="system_cleanup">system_cleanup (Purge Temp Records)</option>
            </select>
          </div>
          <div class="form-group">
            <label>Priority</label>
            <select id="task-priority" class="form-control">
              <option value="10">10 - Critical Priority</option>
              <option value="8" selected>8 - High Priority</option>
              <option value="5">5 - Normal Priority</option>
              <option value="1">1 - Low Priority</option>
            </select>
          </div>
          <div class="form-group">
            <label>Delay Countdown (Seconds)</label>
            <input type="number" id="task-delay" class="form-control" placeholder="0 (Immediate)" min="0" max="86400" value="0" />
          </div>
          <div class="form-group">
            <label>Webhook Callback URL (HMAC Signed)</label>
            <input type="text" id="task-webhook" class="form-control" placeholder="https://api.example.com/webhook (Optional)" />
          </div>
          <div class="form-group" style="align-self: flex-end;">
            <button type="button" onclick="handleDispatch(event)" class="btn btn-success" style="width: 100%; padding: 10px; justify-content: center;" id="submit-btn">
              Enqueue Task
            </button>
          </div>
          <div class="form-group" style="grid-column: 1 / -1; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px; margin-top:-4px; padding-top:4px; border-top:1px solid rgba(255,255,255,0.05);">
            <label style="display:flex; align-items:center; gap:6px; font-size:0.8rem; color:#94A3B8; cursor:pointer; margin:0;">
              <input type="checkbox" id="task-dedup" style="cursor:pointer;" />
              <span>Duplicate Detection Guard (Reject duplicate active tasks)</span>
            </label>
            <span id="dup-status-hint" style="font-size:0.75rem; color:#64748B;">Duplicate guard optional</span>
          </div>
        </div>
      </form>
      <!-- Inline Task Dispatch Feedback / Return Banner -->
      <div id="dispatch-feedback" style="display:none; margin-top:14px; padding:12px 14px; border-radius:8px; font-size:0.85rem;"></div>
    </div>

    <!-- Mobile Segmented Tabs -->
    <div class="mobile-tabs" id="mobile-tabs">
      <button class="tab-pill active" onclick="setMobileView('ALL', event)">All (<span id="m-all">0</span>)</button>
      <button class="tab-pill" onclick="setMobileView('QUEUED', event)">Queued (<span id="m-queued">0</span>)</button>
      <button class="tab-pill" onclick="setMobileView('RUNNING', event)">Running (<span id="m-running">0</span>)</button>
      <button class="tab-pill" onclick="setMobileView('SUCCESS', event)">Success (<span id="m-success">0</span>)</button>
      <button class="tab-pill" onclick="setMobileView('DEAD_LETTERED', event)">DLQ (<span id="m-dlq">0</span>)</button>
    </div>

    <!-- Kanban Grid -->
    <div class="kanban-board-wrapper">
      <div class="kanban-grid">
        <div class="kanban-col" id="col-container-queued">
          <div class="col-header">
            <div style="display:flex; align-items:center; gap:6px;">
              <span>QUEUED / STAGED</span>
              <span id="badge-queued" class="tag">0</span>
            </div>
            <button onclick="startPriorityProcessing()" class="btn btn-success btn-xs" id="col-start-btn" style="background:#10B981; padding:3px 8px; font-weight:700;" title="Run all queued tasks in Priority Order">Process Queue</button>
          </div>
          <div id="col-queued" class="kanban-tasks"></div>
        </div>
        <div class="kanban-col" id="col-container-running">
          <div class="col-header">
            <div style="display:flex; align-items:center; gap:6px;">
              <span>RUNNING (Worker)</span>
              <span id="badge-running" class="tag">0</span>
            </div>
          </div>
          <div id="col-running" class="kanban-tasks"></div>
        </div>
        <div class="kanban-col" id="col-container-success">
          <div class="col-header">
            <div style="display:flex; align-items:center; gap:6px;">
              <span>COMPLETED</span>
              <span id="badge-success" class="tag">0</span>
            </div>
            <button onclick="clearHistory()" class="btn btn-secondary btn-xs" id="clear-history-btn" title="Clear completed tasks history" style="padding:2px 8px; font-weight:600;">Clear History</button>
          </div>
          <div id="col-success" class="kanban-tasks"></div>
        </div>
        <div class="kanban-col" id="col-container-dlq">
          <div class="col-header">
            <div style="display: flex; align-items: center; gap: 6px;">
              <span>DEAD_LETTERED / FAILED</span>
              <span id="badge-dlq" class="tag">0</span>
            </div>
            <button id="replay-all-btn" onclick="replayAllDLQ()" class="btn btn-danger btn-xs" title="Replay all DLQ tasks" style="padding: 2px 7px;">Replay All</button>
          </div>
          <div id="col-dlq" class="kanban-tasks"></div>
        </div>
      </div>
    </div>
  </div>

  <!-- Interactive Task Inspection Modal (Temporal / Flower inspired) -->
  <div class="modal-backdrop" id="task-modal" onclick="handleBackdropClick(event)">
    <div class="modal-dialog">
      <div class="modal-header">
        <div style="display: flex; align-items: center; gap: 8px;">
          <span id="modal-status-badge" class="tag">STATUS</span>
          <h3 id="modal-title">Task Inspection</h3>
        </div>
        <button type="button" class="modal-close" onclick="closeTaskModal()">&times;</button>
      </div>
      <div class="modal-body">
        <!-- Metadata Grid -->
        <div class="detail-grid">
          <div class="detail-item">
            <div class="detail-label">Task ID</div>
            <div class="detail-val">
              <span id="modal-task-id" style="font-size:0.75rem;">-</span>
              <button onclick="copyModalField('modal-task-id', this)" class="btn-xs btn-secondary" style="margin-left:6px;">Copy</button>
            </div>
          </div>
          <div class="detail-item">
            <div class="detail-label">Distributed Trace ID</div>
            <div class="detail-val">
              <span id="modal-trace-id" style="font-size:0.75rem;">-</span>
              <button onclick="copyModalField('modal-trace-id', this)" class="btn-xs btn-secondary" style="margin-left:6px;">Copy</button>
            </div>
          </div>
          <div class="detail-item">
            <div class="detail-label">Task Type & Priority</div>
            <div class="detail-val" id="modal-type-prio">-</div>
          </div>
          <div class="detail-item">
            <div class="detail-label">Scheduled Delay / Webhook</div>
            <div class="detail-val" id="modal-delay-webhook">-</div>
          </div>
          <div class="detail-item">
            <div class="detail-label">Created Timestamp</div>
            <div class="detail-val" id="modal-created">-</div>
          </div>
          <div class="detail-item">
            <div class="detail-label">Updated Timestamp</div>
            <div class="detail-val" id="modal-updated">-</div>
          </div>
          <div class="detail-item">
            <div class="detail-label">Duplicate Status</div>
            <div class="detail-val" id="modal-dup-status"><span style="color:#34D399;">Unique</span></div>
          </div>
        </div>

        <!-- Payload viewer -->
        <div>
          <div class="detail-label" style="margin-bottom: 4px;">Task Payload JSON</div>
          <pre class="code-block" id="modal-payload">{}</pre>
        </div>

        <!-- Result / Error viewer -->
        <div id="modal-result-section">
          <div class="detail-label" style="margin-bottom: 4px;">Execution Result / Error Output</div>
          <pre class="code-block" id="modal-result" style="color: #34D399;">None</pre>
        </div>

        <!-- Execution Attempts Timeline -->
        <div>
          <div class="detail-label" style="margin-bottom: 4px;">Worker Attempt Timeline & Logs</div>
          <div style="background:#0B1120; border:1px solid #1E293B; border-radius:6px; overflow-x:auto;">
            <table class="attempt-table">
              <thead>
                <tr>
                  <th>Attempt</th>
                  <th>Worker ID</th>
                  <th>Duration</th>
                  <th>Status</th>
                  <th>Started At</th>
                </tr>
              </thead>
              <tbody id="modal-attempts-body">
                <tr><td colspan="5" style="color:#94A3B8; text-align:center;">No attempts recorded yet</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
      <div class="modal-footer" id="modal-actions">
        <button type="button" class="btn btn-secondary" onclick="closeTaskModal()">Close</button>
      </div>
    </div>
  </div>

  <!-- CSV Export Preview Modal -->
  <div class="modal-backdrop" id="csv-export-modal" onclick="handleCsvBackdropClick(event)">
    <div class="modal-dialog">
      <div class="modal-header">
        <h3>Task Audit CSV Export</h3>
        <button type="button" class="modal-close" onclick="closeCsvExportModal()">&times;</button>
      </div>
      <div class="modal-body">
        <div style="font-size:0.85rem; color:#94A3B8; margin-bottom:6px;">
          Export generated: <strong id="csv-export-count" style="color:#38BDF8;">0</strong> task records.
        </div>
        <div style="font-size:0.75rem; color:#64748B; margin-bottom:12px;">
          Browser file download was initiated. If your browser blocks popups or file downloads, you can directly download or copy the CSV data below:
        </div>
        <div style="display:flex; gap:10px; margin-bottom:12px; flex-wrap:wrap;">
          <button type="button" class="btn btn-primary btn-xs" onclick="triggerCsvDownload()" id="modal-download-btn">Download CSV File</button>
          <button type="button" class="btn btn-secondary btn-xs" onclick="copyCsvPreview(this)" id="modal-copy-btn">Copy to Clipboard</button>
        </div>
        <div class="detail-label" style="margin-bottom: 4px;">CSV Output Stream</div>
        <pre class="code-block" id="csv-export-preview" style="max-height: 240px; white-space: pre; overflow-x: auto; font-size: 0.76rem; color: #34D399;"></pre>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-secondary" onclick="closeCsvExportModal()">Close</button>
      </div>
    </div>
  </div>

  <!-- New Task Creation Modal Dialog (Jira/Linear inspired) -->
  <div class="modal-backdrop" id="new-task-modal" onclick="handleNewTaskBackdropClick(event)">
    <div class="modal-dialog">
      <div class="modal-header">
        <div style="display:flex; align-items:center; gap:8px;">
          <span class="tag tag-wh">DISPATCH</span>
          <h3>Dispatch New Task to Cluster</h3>
        </div>
        <button type="button" class="modal-close" onclick="closeNewTaskModal()">&times;</button>
      </div>
      <div class="modal-body">
        <form id="modal-task-form" onsubmit="handleModalDispatch(event); return false;">
          <div class="form-group" style="margin-bottom:12px;">
            <label style="display:block; font-size:0.8rem; color:#94A3B8; margin-bottom:4px; font-weight:600;">Task Title</label>
            <input type="text" id="modal-task-title" class="form-control" placeholder="e.g. Ingest Customer Ledger" style="width:100%;" />
          </div>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:12px;">
            <div class="form-group">
              <label style="display:block; font-size:0.8rem; color:#94A3B8; margin-bottom:4px; font-weight:600;">Handler Type</label>
              <select id="modal-task-type" class="form-control" style="width:100%;">
                <option value="report_generation">report_generation (PDF Export)</option>
                <option value="data_processing">data_processing (ETL Batch)</option>
                <option value="email_dispatch">email_dispatch (Transactional Email)</option>
                <option value="system_cleanup">system_cleanup (Purge Temp Records)</option>
              </select>
            </div>
            <div class="form-group">
              <label style="display:block; font-size:0.8rem; color:#94A3B8; margin-bottom:4px; font-weight:600;">Priority</label>
              <select id="modal-task-priority" class="form-control" style="width:100%;">
                <option value="10">10 - Critical Priority</option>
                <option value="8" selected>8 - High Priority</option>
                <option value="5">5 - Normal Priority</option>
                <option value="1">1 - Low Priority</option>
              </select>
            </div>
          </div>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:12px;">
            <div class="form-group">
              <label style="display:block; font-size:0.8rem; color:#94A3B8; margin-bottom:4px; font-weight:600;">Delay Countdown (Seconds)</label>
              <input type="number" id="modal-task-delay" class="form-control" value="0" min="0" max="86400" style="width:100%;" />
            </div>
            <div class="form-group">
              <label style="display:block; font-size:0.8rem; color:#94A3B8; margin-bottom:4px; font-weight:600;">Webhook Callback URL</label>
              <input type="text" id="modal-task-webhook" class="form-control" placeholder="https://api.example.com/webhook (Optional)" style="width:100%;" />
            </div>
          </div>
          <div style="display:flex; align-items:center; gap:8px; margin-bottom:14px;">
            <input type="checkbox" id="modal-task-dedup" style="cursor:pointer;" />
            <label for="modal-task-dedup" style="font-size:0.8rem; color:#94A3B8; cursor:pointer; margin:0;">Duplicate Detection Guard (Reject duplicate active tasks)</label>
          </div>
          <button type="submit" class="btn btn-success" id="modal-submit-btn" style="width:100%; padding:10px; justify-content:center; font-weight:700;">
            Enqueue Task to Cluster
          </button>
        </form>

        <!-- Live Modal Dispatch Feedback / Return Panel -->
        <div id="modal-dispatch-feedback" style="display:none; margin-top:14px; padding:12px 14px; border-radius:8px; font-size:0.85rem;"></div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-secondary" onclick="closeNewTaskModal()">Close</button>
      </div>
    </div>
  </div>

  <script>
    function getStorageToken() {
      try { return localStorage.getItem('cloudtask_token') || ''; } catch (e) { return ''; }
    }
    function setStorageToken(val) {
      try {
        if (val) localStorage.setItem('cloudtask_token', val);
        else localStorage.removeItem('cloudtask_token');
      } catch (e) {}
    }

    // Smart Base URL resolver: seamlessly supports same-origin, file://, and static web servers (like port 8080)
    let detectedApiBase = '';

    async function detectApiBase() {
      if (window.location.protocol === 'http:' && window.location.port === '8000') {
        detectedApiBase = '';
        return;
      }
      if (window.location.hostname === 'cloudtask-platform.onrender.com') {
        detectedApiBase = '';
        return;
      }
      try {
        const ctrl = new AbortController();
        const tid = setTimeout(() => ctrl.abort(), 800);
        const testRes = await fetch('http://localhost:8000/health/live', { signal: ctrl.signal });
        clearTimeout(tid);
        if (testRes.ok) {
          detectedApiBase = 'http://localhost:8000';
          return;
        }
      } catch (e) {}
      detectedApiBase = 'https://cloudtask-platform.onrender.com';
    }

    function getApiUrl(path) {
      const cleanPath = path.startsWith('/') ? path : '/' + path;
      if (detectedApiBase) return detectedApiBase + cleanPath;
      if (window.location.protocol === 'file:' || (window.location.port && window.location.port !== '8000')) {
        return 'https://cloudtask-platform.onrender.com' + cleanPath;
      }
      return cleanPath;
    }

    // Enterprise Toast Notification & Action Center
    function showToast(title, message, type = 'info', actionBtn = null) {
      const container = document.getElementById('toast-container');
      if (!container) return;

      const toast = document.createElement('div');
      toast.className = `toast-msg toast-${type}`;

      let actionHtml = '';
      if (actionBtn && actionBtn.label) {
        actionHtml = `<div class="toast-actions"><button type="button" class="btn btn-xs ${type === 'success' ? 'btn-success' : 'btn-primary'}" id="toast-act-btn">${actionBtn.label}</button></div>`;
      }

      toast.innerHTML = `
        <div class="toast-header">
          <span class="toast-title">${title}</span>
          <button type="button" class="toast-close" title="Dismiss">&times;</button>
        </div>
        <div class="toast-body">${message}</div>
        ${actionHtml}
      `;

      const closeBtn = toast.querySelector('.toast-close');
      const dismiss = () => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(-10px)';
        setTimeout(() => { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 300);
      };
      closeBtn.onclick = dismiss;

      if (actionBtn && actionBtn.onClick) {
        const actBtn = toast.querySelector('#toast-act-btn');
        if (actBtn) {
          actBtn.onclick = () => {
            try { actionBtn.onClick(); } catch(e) { console.warn(e); }
            dismiss();
          };
        }
      }

      const timer = setTimeout(dismiss, 5500);
      toast.onmouseenter = () => clearTimeout(timer);

      container.appendChild(toast);
    }

    let authToken = getStorageToken();
    let allTasks = [];
    let currentSearch = '';
    let currentTypeFilter = '';
    let currentPriorityFilter = '';
    let currentDuplicateFilter = '';
    let activeMobileTab = 'ALL';
    let currentModalTaskId = null;

    let isServerOnline = true;

    function updateConnectionStatus(online) {
      isServerOnline = online;
      const alertEl = document.getElementById('offline-alert');
      if (alertEl) alertEl.style.display = online ? 'none' : 'block';
    }

    async function ensureAuth(forceRefresh = false) {
      if (authToken && !forceRefresh) return authToken;
      try {
        const res = await fetch(getApiUrl('/api/v1/auth/login'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: 'admin@cloudtask.dev', password: 'AdminSecurePass123!' })
        });
        if (res.ok) {
          const data = await res.json();
          authToken = data.access_token;
          setStorageToken(authToken);
          updateConnectionStatus(true);
          return authToken;
        } else {
          if (res.status === 404) updateConnectionStatus(false);
          setStorageToken('');
          authToken = '';
        }
      } catch (err) {
        console.warn('Auto-login error:', err);
        updateConnectionStatus(false);
      }
      return authToken;
    }

    async function fetchTasks() {
      let token = await ensureAuth();
      if (!token) return;

      try {
        let res = await fetch(getApiUrl('/api/v1/tasks?limit=100'), {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (res.status === 401) {
          token = await ensureAuth(true);
          if (token) {
            res = await fetch(getApiUrl('/api/v1/tasks?limit=100'), {
              headers: { 'Authorization': `Bearer ${token}` }
            });
          }
        }
        if (!res.ok) {
          if (res.status === 404) updateConnectionStatus(false);
          return;
        }

        updateConnectionStatus(true);
        const data = await res.json();
        allTasks = data.tasks || [];
        applyFiltersAndRender();
        checkDuplicateInline();
      } catch (err) {
        console.error('Fetch error:', err);
        updateConnectionStatus(false);
      }
    }

    function checkDuplicateInline() {
      const title = (document.getElementById('task-title').value || '').trim();
      const type = document.getElementById('task-type').value;
      const warnEl = document.getElementById('duplicate-warning');
      const inputEl = document.getElementById('task-title');
      const hintEl = document.getElementById('dup-status-hint');

      if (!title) {
        if (warnEl) warnEl.style.display = 'none';
        if (inputEl) inputEl.style.borderColor = '';
        if (hintEl) hintEl.innerText = 'Active deduplication enabled';
        return;
      }

      // Check against allTasks for active tasks (QUEUED, PENDING, RUNNING) with identical title and task_type
      const activeDup = allTasks.find(t =>
        ['QUEUED', 'PENDING', 'RUNNING'].includes(t.status) &&
        (t.title || '').trim().toLowerCase() === title.toLowerCase() &&
        t.task_type === type
      );

      if (activeDup) {
        if (warnEl) {
          warnEl.innerText = `Duplicate Detected (${activeDup.status})`;
          warnEl.style.display = 'inline';
        }
        if (inputEl) inputEl.style.borderColor = '#F59E0B';
        if (hintEl) hintEl.innerText = `Warning: Active task "${title}" already exists in ${activeDup.status} state`;
      } else {
        if (warnEl) warnEl.style.display = 'none';
        if (inputEl) inputEl.style.borderColor = '';
        if (hintEl) hintEl.innerText = 'No active duplicate detected';
      }
    }

    function openNewTaskModal() {
      const modal = document.getElementById('new-task-modal');
      const titleInput = document.getElementById('modal-task-title');
      const sampleNum = Math.floor(1000 + Math.random() * 9000);
      const handlers = ['report_generation', 'data_processing', 'email_dispatch', 'system_cleanup'];
      const typeSelect = document.getElementById('modal-task-type');
      const currentIdx = handlers.indexOf(typeSelect ? typeSelect.value : '');
      const nextType = handlers[(currentIdx + 1) % handlers.length];

      if (titleInput) {
        titleInput.value = 'Pipeline Task #' + sampleNum;
      }
      if (typeSelect) {
        typeSelect.value = nextType;
      }

      // Reset feedback and submit button in modal
      const fb = document.getElementById('modal-dispatch-feedback');
      if (fb) fb.style.display = 'none';

      const submitBtn = document.getElementById('modal-submit-btn');
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.innerText = 'Enqueue Task to Cluster';
      }

      // Pre-fill inline form as well
      const inlineTitle = document.getElementById('task-title');
      if (inlineTitle) inlineTitle.value = 'Pipeline Task #' + sampleNum;

      if (modal) {
        modal.style.display = 'flex';
      }
      if (titleInput) {
        setTimeout(() => { titleInput.focus(); titleInput.select(); }, 60);
      }

      showToast(
        'New Task Dialog Opened',
        `Pre-populated 'Pipeline Task #${sampleNum}' (${nextType}). Configure options and click Enqueue Task.`,
        'info'
      );
    }

    function closeNewTaskModal() {
      const modal = document.getElementById('new-task-modal');
      if (modal) modal.style.display = 'none';
    }

    function handleNewTaskBackdropClick(e) {
      if (e.target.id === 'new-task-modal') closeNewTaskModal();
    }

    function toggleDispatcher() {
      openNewTaskModal();
    }

    async function handleModalDispatch(e) {
      if (e && e.preventDefault) e.preventDefault();
      const btn = document.getElementById('modal-submit-btn');
      const fb = document.getElementById('modal-dispatch-feedback');
      if (btn) {
        btn.disabled = true;
        btn.innerText = 'Dispatching to Cluster...';
      }
      if (fb) {
        fb.style.display = 'block';
        fb.style.background = 'rgba(59, 130, 246, 0.12)';
        fb.style.border = '1px solid rgba(59, 130, 246, 0.35)';
        fb.style.color = '#93C5FD';
        fb.innerHTML = '<strong>Submitting task to cluster...</strong>';
      }

      let token = await ensureAuth();
      let title = (document.getElementById('modal-task-title').value || '').trim();
      if (!title) {
        title = 'Quick Task #' + Math.floor(1000 + Math.random() * 9000);
      }
      const task_type = document.getElementById('modal-task-type').value;
      const priority = parseInt(document.getElementById('modal-task-priority').value, 10);
      const delay = parseInt(document.getElementById('modal-task-delay').value, 10) || 0;
      const webhook = (document.getElementById('modal-task-webhook').value || '').trim() || null;
      const dedupCheckbox = document.getElementById('modal-task-dedup');
      const preventDuplicates = dedupCheckbox ? dedupCheckbox.checked : false;

      try {
        const payloadBody = {
          title: title,
          task_type: task_type,
          priority: priority,
          delay_seconds: delay,
          prevent_duplicates: preventDuplicates,
          payload: { timestamp: Date.now() }
        };
        if (webhook) payloadBody.webhook_url = webhook;

        let res = await fetch(getApiUrl('/api/v1/tasks'), {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
          },
          body: JSON.stringify(payloadBody)
        });

        if (res.status === 401) {
          token = await ensureAuth(true);
          res = await fetch(getApiUrl('/api/v1/tasks'), {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify(payloadBody)
          });
        }

        if (res.ok) {
          const createdTask = await res.json().catch(() => null);
          if (btn) btn.innerText = 'Enqueued Successfully';

          if (createdTask) {
            allTasks.unshift(createdTask);
            applyFiltersAndRender();

            showToast(
              'Task Enqueued Successfully',
              `[${createdTask.id.slice(0, 8)}] "${createdTask.title}" (${createdTask.task_type}, P${createdTask.priority}). Status: ${createdTask.status}.`,
              'success'
            );

            if (fb) {
              const startProcessingBtnHtml = currentExecutionMode === 'manual'
                ? `<button type="button" class="btn btn-xs btn-success" onclick="startPriorityProcessing(); closeNewTaskModal();" style="background:#10B981;">Start Processing</button>`
                : '';

              fb.style.display = 'block';
              fb.style.background = 'rgba(16, 185, 129, 0.12)';
              fb.style.border = '1px solid rgba(16, 185, 129, 0.35)';
              fb.style.color = '#34D399';
              fb.innerHTML = `
                <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:8px;">
                  <div>
                    <div style="font-weight:700; font-size:0.95rem; color:#FFFFFF; margin-bottom:4px;">
                      Task Accepted (Status: <span id="modal-fb-status">${createdTask.status}</span>)
                    </div>
                    <div style="color:#A7F3D0; font-size:0.85rem; margin-bottom:4px;">
                      <strong>${createdTask.title}</strong> [${createdTask.task_type}] &bull; Priority P${createdTask.priority}
                    </div>
                    <div style="font-family:monospace; font-size:0.75rem; color:#94A3B8;">
                      Task ID: ${createdTask.id} &bull; Trace: ${createdTask.trace_id || 'N/A'}
                    </div>
                    <div id="modal-fb-result" style="margin-top:6px; font-size:0.8rem; color:#38BDF8; font-family:monospace;">
                      ${currentExecutionMode === 'manual' ? 'Staged in queue. Tap Start Processing to execute.' : 'Worker executing task in fleet...'}
                    </div>
                  </div>
                  <div style="display:flex; gap:8px; align-self:flex-start; flex-wrap:wrap;">
                    <button type="button" class="btn btn-xs btn-primary" onclick="closeNewTaskModal(); openTaskModal('${createdTask.id}');">Inspect</button>
                    ${startProcessingBtnHtml}
                  </div>
                </div>
              `;

              pollModalTaskResult(createdTask.id);
            }
          }
          fetchTasks();
        } else {
          const errData = await res.json().catch(() => ({}));
          let errMsg = (res.status === 409)
            ? 'Duplicate Task Blocked (409 Conflict): ' + (errData.detail || 'An active task with this title exists.')
            : 'Task dispatch failed (' + res.status + '): ' + (errData.detail || 'Service temporarily unavailable.');
          showToast('Dispatch Conflict', errMsg, 'error');
          if (fb) {
            fb.style.display = 'block';
            fb.style.background = 'rgba(239, 68, 68, 0.15)';
            fb.style.border = '1px solid rgba(239, 68, 68, 0.4)';
            fb.style.color = '#FCA5A5';
            fb.innerHTML = `<strong>Error:</strong> ${errMsg}`;
          }
          if (btn) {
            btn.disabled = false;
            btn.innerText = 'Enqueue Task to Cluster';
          }
        }
      } catch (err) {
        showToast('Dispatch Error', err.message, 'error');
        if (fb) {
          fb.style.display = 'block';
          fb.style.background = 'rgba(239, 68, 68, 0.15)';
          fb.style.border = '1px solid rgba(239, 68, 68, 0.4)';
          fb.style.color = '#FCA5A5';
          fb.innerHTML = `<strong>Error:</strong> ${err.message}`;
        }
        if (btn) {
          btn.disabled = false;
          btn.innerText = 'Enqueue Task to Cluster';
        }
      }
    }

    function pollModalTaskResult(taskId) {
      let attempts = 0;
      const interval = setInterval(async () => {
        attempts++;
        await fetchTasks();
        const t = allTasks.find(x => x.id === taskId);
        if (t) {
          const statusEl = document.getElementById('modal-fb-status');
          const resEl = document.getElementById('modal-fb-result');
          if (statusEl) statusEl.innerText = t.status;
          if (t.status === 'SUCCESS') {
            if (resEl) {
              resEl.style.color = '#34D399';
              resEl.innerHTML = `<strong>Result Returned:</strong> <code>${JSON.stringify(t.result || { status: 'SUCCESS' })}</code>`;
            }
            clearInterval(interval);
          } else if (t.status === 'FAILED' || t.status === 'DEAD_LETTERED') {
            if (resEl) {
              resEl.style.color = '#F87171';
              resEl.innerHTML = `<strong>Execution Failed:</strong> ${t.error_message || 'Task failed'}`;
            }
            clearInterval(interval);
          }
        }
        if (attempts >= 30) clearInterval(interval);
      }, 350);
    }

    function handleSearch(val) {
      currentSearch = val.toLowerCase().trim();
      applyFiltersAndRender();
    }

    function applyFilters() {
      currentTypeFilter = document.getElementById('filter-type').value;
      currentPriorityFilter = document.getElementById('filter-priority').value;
      const dupSelect = document.getElementById('filter-duplicate');
      currentDuplicateFilter = dupSelect ? dupSelect.value : '';
      applyFiltersAndRender();
    }

    function setMobileView(tab, e) {
      activeMobileTab = tab;
      const pills = document.querySelectorAll('.tab-pill');
      pills.forEach(p => p.classList.remove('active'));
      const activePill = (e && e.target) ? e.target : document.querySelector(`.tab-pill[onclick*="'${tab}'"]`);
      if (activePill) activePill.classList.add('active');

      const colMap = {
        QUEUED: document.getElementById('col-container-queued'),
        RUNNING: document.getElementById('col-container-running'),
        SUCCESS: document.getElementById('col-container-success'),
        DEAD_LETTERED: document.getElementById('col-container-dlq'),
      };

      if (tab === 'ALL') {
        Object.values(colMap).forEach(col => col.classList.remove('mobile-hidden'));
      } else {
        Object.entries(colMap).forEach(([k, col]) => {
          if (k === tab) col.classList.remove('mobile-hidden');
          else col.classList.add('mobile-hidden');
        });
      }
    }

    function applyFiltersAndRender() {
      const sigCounts = {};
      allTasks.forEach(task => {
        const sig = `${task.task_type}::${(task.title || '').trim().toLowerCase()}`;
        sigCounts[sig] = (sigCounts[sig] || 0) + 1;
      });

      const filtered = allTasks.filter(task => {
        if (currentTypeFilter && task.task_type !== currentTypeFilter) return false;
        if (currentPriorityFilter && String(task.priority) !== currentPriorityFilter) return false;
        if (currentDuplicateFilter === 'duplicates') {
          const sig = `${task.task_type}::${(task.title || '').trim().toLowerCase()}`;
          if ((sigCounts[sig] || 0) <= 1) return false;
        }
        if (currentSearch) {
          const matchTitle = (task.title || '').toLowerCase().includes(currentSearch);
          const matchId = (task.id || '').toLowerCase().includes(currentSearch);
          const matchTrace = (task.trace_id || '').toLowerCase().includes(currentSearch);
          if (!matchTitle && !matchId && !matchTrace) return false;
        }
        return true;
      });

      renderColumns(filtered, sigCounts);
    }

    function renderColumns(tasks, sigCounts = {}) {
      const cols = {
        QUEUED: document.getElementById('col-queued'),
        RUNNING: document.getElementById('col-running'),
        SUCCESS: document.getElementById('col-success'),
        DEAD_LETTERED: document.getElementById('col-dlq'),
      };

      Object.values(cols).forEach(col => col.innerHTML = '');

      let counts = { total: tasks.length, queued: 0, running: 0, success: 0, dlq: 0 };

      tasks.forEach(task => {
        const status = task.status;
        if (status === 'QUEUED' || status === 'PENDING') counts.queued++;
        else if (status === 'RUNNING') counts.running++;
        else if (status === 'SUCCESS') counts.success++;
        else counts.dlq++;

        const card = document.createElement('div');
        card.className = 'task-card';
        card.onclick = (e) => {
          if (e.target.tagName === 'BUTTON') return;
          openTaskModal(task.id);
        };

        let progressHtml = '';
        if (status === 'RUNNING') {
          progressHtml = `
            <div class="progress-bar">
              <div class="progress-fill" style="width: ${Math.max(task.progress || 25, 20)}%;"></div>
            </div>
            <div style="font-size:0.72rem; color:#38BDF8; font-weight:600;">Executing: ${task.progress || 25}%</div>
          `;
        }

        let delayBadge = '';
        if (task.delay_seconds && task.delay_seconds > 0) {
          delayBadge = `<span class="tag tag-delay">Delay: ${task.delay_seconds}s</span>`;
        }
        let webhookBadge = '';
        if (task.webhook_url) {
          webhookBadge = `<span class="tag tag-wh" title="${task.webhook_url}">Webhook</span>`;
        }

        const sig = `${task.task_type}::${(task.title || '').trim().toLowerCase()}`;
        const isDup = (sigCounts[sig] || 0) > 1;
        let dupBadge = '';
        if (isDup) {
          dupBadge = `<span class="tag" style="background:rgba(245,158,11,0.15); color:#F59E0B; border:1px solid rgba(245,158,11,0.3);" title="Duplicate task: ${sigCounts[sig]} instances detected">Duplicate (${sigCounts[sig]})</span>`;
        }

        let actionHtml = `
          <div class="card-actions">
            <button onclick="openTaskModal('${task.id}')" class="btn btn-secondary btn-xs">Inspect</button>
        `;
        if (status === 'QUEUED' || status === 'RUNNING' || status === 'PENDING') {
          actionHtml += `<button onclick="cancelTask('${task.id}')" class="btn btn-danger btn-xs">Cancel</button>`;
        } else if (status === 'FAILED' || status === 'DEAD_LETTERED') {
          actionHtml += `<button onclick="retryTask('${task.id}')" class="btn btn-success btn-xs">Retry</button>`;
        }
        actionHtml += `</div>`;

        let snippet = '';
        if (task.error_message) {
          snippet = `<div style="font-size:0.7rem; color:#F87171; margin-top:5px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${task.error_message}</div>`;
        } else if (task.result) {
          snippet = `<div style="font-size:0.7rem; color:#34D399; margin-top:5px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">Output: ${JSON.stringify(task.result)}</div>`;
        }

        card.innerHTML = `
          <div class="task-title">${task.title}</div>
          <div class="task-meta">
            <span class="tag tag-prio">Prio ${task.priority}</span>
            <span class="tag">${task.task_type}</span>
            <span class="tag">Att: ${task.current_attempt}/${task.max_retries}</span>
            ${delayBadge}
            ${webhookBadge}
            ${dupBadge}
          </div>
          ${progressHtml}
          ${snippet}
          ${actionHtml}
        `;

        if (status === 'QUEUED' || status === 'PENDING') cols.QUEUED.appendChild(card);
        else if (status === 'RUNNING') cols.RUNNING.appendChild(card);
        else if (status === 'SUCCESS') cols.SUCCESS.appendChild(card);
        else cols.DEAD_LETTERED.appendChild(card);
      });

      if (counts.queued === 0) cols.QUEUED.innerHTML = '<div style="color:#64748B; font-size:0.75rem; text-align:center; margin-top:24px; padding:12px; border:1px dashed #1E293B; border-radius:6px;">No queued tasks</div>';
      if (counts.running === 0) cols.RUNNING.innerHTML = '<div style="color:#64748B; font-size:0.75rem; text-align:center; margin-top:24px; padding:12px; border:1px dashed #1E293B; border-radius:6px;">No running tasks</div>';
      if (counts.success === 0) cols.SUCCESS.innerHTML = '<div style="color:#64748B; font-size:0.75rem; text-align:center; margin-top:24px; padding:12px; border:1px dashed #1E293B; border-radius:6px;">No completed tasks</div>';
      if (counts.dlq === 0) cols.DEAD_LETTERED.innerHTML = '<div style="color:#64748B; font-size:0.75rem; text-align:center; margin-top:24px; padding:12px; border:1px dashed #1E293B; border-radius:6px;">DLQ is empty</div>';

      // Update counters
      document.getElementById('total-count').innerText = counts.total;
      document.getElementById('queued-count').innerText = counts.queued;
      document.getElementById('running-count').innerText = counts.running;
      document.getElementById('success-count').innerText = counts.success;
      document.getElementById('dlq-count').innerText = counts.dlq;

      const startCountEl = document.getElementById('start-count');
      if (startCountEl) startCountEl.innerText = counts.queued;

      document.getElementById('badge-queued').innerText = counts.queued;
      document.getElementById('badge-running').innerText = counts.running;
      document.getElementById('badge-success').innerText = counts.success;
      document.getElementById('badge-dlq').innerText = counts.dlq;

      document.getElementById('m-all').innerText = counts.total;
      document.getElementById('m-queued').innerText = counts.queued;
      document.getElementById('m-running').innerText = counts.running;
      document.getElementById('m-success').innerText = counts.success;
      document.getElementById('m-dlq').innerText = counts.dlq;

      document.getElementById('filter-count-badge').innerText = `Showing ${tasks.length} of ${allTasks.length} tasks`;
    }

    // Modal Inspection Logic
    function openTaskModal(taskId) {
      const task = allTasks.find(t => t.id === taskId);
      if (!task) return;
      currentModalTaskId = taskId;

      document.getElementById('modal-title').innerText = task.title;
      document.getElementById('modal-task-id').innerText = task.id;
      document.getElementById('modal-trace-id').innerText = task.trace_id || 'N/A';
      document.getElementById('modal-type-prio').innerText = `${task.task_type} (Priority: ${task.priority})`;
      document.getElementById('modal-delay-webhook').innerText = `Delay: ${task.delay_seconds || 0}s | Webhook: ${task.webhook_url ? 'Configured' : 'None'}`;
      document.getElementById('modal-created').innerText = new Date(task.created_at).toLocaleString();
      document.getElementById('modal-updated').innerText = new Date(task.updated_at).toLocaleString();

      const dupStatusEl = document.getElementById('modal-dup-status');
      if (dupStatusEl) {
        const sig = `${task.task_type}::${(task.title || '').trim().toLowerCase()}`;
        const dupList = allTasks.filter(t => `${t.task_type}::${(t.title || '').trim().toLowerCase()}` === sig);
        if (dupList.length > 1) {
          dupStatusEl.innerHTML = `<span style="color:#F59E0B; font-weight:600;">Duplicate Detected (${dupList.length} instances in system)</span>`;
        } else {
          dupStatusEl.innerHTML = `<span style="color:#34D399;">Unique (No duplicates detected)</span>`;
        }
      }

      const badge = document.getElementById('modal-status-badge');
      badge.innerText = task.status;
      if (task.status === 'SUCCESS') badge.className = 'tag tag-wh';
      else if (task.status === 'RUNNING') badge.className = 'tag';
      else if (task.status === 'DEAD_LETTERED' || task.status === 'FAILED') badge.className = 'tag tag-prio';
      else badge.className = 'tag';

      document.getElementById('modal-payload').innerText = JSON.stringify(task.payload || {}, null, 2);

      const resultBox = document.getElementById('modal-result');
      if (task.error_message) {
        resultBox.style.color = '#F87171';
        resultBox.innerText = `Error: ${task.error_message}`;
      } else if (task.result) {
        resultBox.style.color = '#34D399';
        resultBox.innerText = JSON.stringify(task.result, null, 2);
      } else {
        resultBox.style.color = '#94A3B8';
        resultBox.innerText = 'No result output recorded yet.';
      }

      // Render attempts table
      const tbody = document.getElementById('modal-attempts-body');
      tbody.innerHTML = '';
      if (task.attempts && task.attempts.length > 0) {
        task.attempts.forEach(att => {
          const row = document.createElement('tr');
          row.innerHTML = `
            <td><strong>#${att.attempt_number}</strong></td>
            <td><code>${att.worker_id}</code></td>
            <td>${att.duration_ms ? att.duration_ms + 'ms' : '-'}</td>
            <td><span class="tag">${att.status}</span></td>
            <td>${new Date(att.started_at).toLocaleTimeString()}</td>
          `;
          tbody.appendChild(row);
        });
      } else {
        tbody.innerHTML = '<tr><td colspan="5" style="color:#94A3B8; text-align:center;">No worker attempts recorded yet</td></tr>';
      }

      // Modal action buttons
      const actionContainer = document.getElementById('modal-actions');
      actionContainer.innerHTML = '';
      if (task.status === 'QUEUED' || task.status === 'RUNNING' || task.status === 'PENDING') {
        const cancelBtn = document.createElement('button');
        cancelBtn.className = 'btn btn-danger';
        cancelBtn.innerText = 'Cancel Task';
        cancelBtn.onclick = () => { cancelTask(task.id); closeTaskModal(); };
        actionContainer.appendChild(cancelBtn);
      } else if (task.status === 'FAILED' || task.status === 'DEAD_LETTERED') {
        const retryBtn = document.createElement('button');
        retryBtn.className = 'btn btn-success';
        retryBtn.innerText = 'Retry Task';
        retryBtn.onclick = () => { retryTask(task.id); closeTaskModal(); };
        actionContainer.appendChild(retryBtn);
      }

      const closeBtn = document.createElement('button');
      closeBtn.className = 'btn btn-secondary';
      closeBtn.innerText = 'Close';
      closeBtn.onclick = closeTaskModal;
      actionContainer.appendChild(closeBtn);

      document.getElementById('task-modal').style.display = 'flex';
    }

    function closeTaskModal() {
      document.getElementById('task-modal').style.display = 'none';
      currentModalTaskId = null;
    }

    function handleBackdropClick(e) {
      if (e.target.id === 'task-modal') closeTaskModal();
    }

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeTaskModal();
        closeNewTaskModal();
        closeCsvExportModal();
      }
    });

    function copyModalField(elementId, btn) {
      const text = document.getElementById(elementId).innerText;
      if (navigator.clipboard) {
        navigator.clipboard.writeText(text);
        btn.innerText = 'Copied';
        setTimeout(() => btn.innerText = 'Copy', 1500);
      }
    }

    async function handleDispatch(e) {
      if (e && e.preventDefault) e.preventDefault();
      const btn = document.getElementById('submit-btn');
      const fb = document.getElementById('dispatch-feedback');
      btn.disabled = true;
      btn.innerText = 'Dispatching...';
      if (fb) {
        fb.style.display = 'block';
        fb.style.background = 'rgba(59, 130, 246, 0.12)';
        fb.style.border = '1px solid rgba(59, 130, 246, 0.35)';
        fb.style.color = '#93C5FD';
        fb.innerHTML = '<strong>Submitting task to cluster...</strong>';
      }

      let token = await ensureAuth();
      let title = (document.getElementById('task-title').value || '').trim();
      if (!title) {
        title = 'Quick Task #' + Math.floor(1000 + Math.random() * 9000);
      }
      const task_type = document.getElementById('task-type').value;
      const priority = parseInt(document.getElementById('task-priority').value, 10);
      const delay = parseInt(document.getElementById('task-delay').value, 10) || 0;
      const webhook = (document.getElementById('task-webhook').value || '').trim() || null;
      const dedupCheckbox = document.getElementById('task-dedup');
      const preventDuplicates = dedupCheckbox ? dedupCheckbox.checked : false;

      try {
        const payloadBody = {
          title: title,
          task_type: task_type,
          priority: priority,
          delay_seconds: delay,
          prevent_duplicates: preventDuplicates,
          payload: { timestamp: Date.now() }
        };
        if (webhook) payloadBody.webhook_url = webhook;

        let res = await fetch(getApiUrl('/api/v1/tasks'), {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
          },
          body: JSON.stringify(payloadBody)
        });

        // Token expired check
        if (res.status === 401) {
          token = await ensureAuth(true);
          res = await fetch(getApiUrl('/api/v1/tasks'), {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify(payloadBody)
          });
        }

        if (res.ok) {
          const createdTask = await res.json().catch(() => null);
          document.getElementById('task-title').value = '';
          document.getElementById('task-webhook').value = '';
          document.getElementById('task-delay').value = '0';
          checkDuplicateInline();
          btn.innerText = 'Enqueued';
          setTimeout(() => {
            btn.disabled = false;
            btn.innerText = 'Enqueue Task';
          }, 1500);

          if (createdTask) {
            allTasks.unshift(createdTask);
            applyFiltersAndRender();

            showToast(
              'Task Enqueued Successfully',
              `[${createdTask.id.slice(0, 8)}] "${createdTask.title}" (${createdTask.task_type}, P${createdTask.priority}). Status: ${createdTask.status}.`,
              'success',
              { label: 'Inspect Task', onClick: () => openTaskModal(createdTask.id) }
            );

            if (fb) {
              const startProcessingBtnHtml = currentExecutionMode === 'manual'
                ? `<button type="button" class="btn btn-xs btn-success" onclick="startPriorityProcessing()" style="background:#10B981;">Start Processing</button>`
                : '';

              fb.style.display = 'block';
              fb.style.background = 'rgba(16, 185, 129, 0.12)';
              fb.style.border = '1px solid rgba(16, 185, 129, 0.35)';
              fb.style.color = '#34D399';
              fb.innerHTML = `
                <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:8px;">
                  <div>
                    <div style="font-weight:700; font-size:0.95rem; color:#FFFFFF; margin-bottom:4px;">
                      Task Accepted by Cluster (Status: <span id="fb-status-${createdTask.id}">${createdTask.status}</span>)
                    </div>
                    <div style="color:#A7F3D0; font-size:0.85rem; margin-bottom:4px;">
                      <strong>${createdTask.title}</strong> [${createdTask.task_type}] &bull; Priority P${createdTask.priority}
                    </div>
                    <div style="font-family:monospace; font-size:0.75rem; color:#94A3B8;">
                      Task ID: ${createdTask.id} &bull; Trace: ${createdTask.trace_id || 'N/A'}
                    </div>
                    <div id="fb-result-${createdTask.id}" style="margin-top:6px; font-size:0.8rem; color:#38BDF8; font-family:monospace;">
                      ${currentExecutionMode === 'manual' ? 'Staged in queue. Click Start Processing to execute.' : 'Worker executing task...'}
                    </div>
                  </div>
                  <div style="display:flex; gap:8px; align-self:flex-start; flex-wrap:wrap;">
                    <button type="button" class="btn btn-xs btn-primary" onclick="openTaskModal('${createdTask.id}')">Inspect</button>
                    ${startProcessingBtnHtml}
                  </div>
                </div>
              `;
              fb.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

              // Poll for live task execution outcome and display returned result
              pollTaskResult(createdTask.id);
            }
          }
          fetchTasks();
        } else {
          const errData = await res.json().catch(() => ({}));
          let errMsg = '';
          if (res.status === 409) {
            errMsg = 'Duplicate Task Blocked (409 Conflict): ' + (errData.detail || 'A matching active task already exists in the queue.') + '<br><small style="color:#CBD5E1;">Tip: Uncheck "Duplicate Detection Guard" below if you want to allow duplicate tasks.</small>';
          } else if (res.status === 404) {
            errMsg = 'Endpoint Not Found (404): Backend task service is not running on this port.';
          } else {
            errMsg = 'Task dispatch failed (' + res.status + '): ' + (errData.detail || 'Service temporarily unavailable. Please retry.');
          }
          showToast('Task Dispatch Conflict', errMsg.replace(/<[^>]*>/g, ''), 'error');
          if (fb) {
            fb.style.display = 'block';
            fb.style.background = 'rgba(239, 68, 68, 0.15)';
            fb.style.border = '1px solid rgba(239, 68, 68, 0.4)';
            fb.style.color = '#FCA5A5';
            fb.innerHTML = `<strong>Error:</strong> ${errMsg}`;
            fb.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
          }
          btn.disabled = false;
          btn.innerText = 'Enqueue Task';
        }
      } catch (err) {
        showToast('Dispatch Error', err.message, 'error');
        const fbErr = document.getElementById('dispatch-feedback');
        if (fbErr) {
          fbErr.style.display = 'block';
          fbErr.style.background = 'rgba(239, 68, 68, 0.15)';
          fbErr.style.border = '1px solid rgba(239, 68, 68, 0.4)';
          fbErr.style.color = '#FCA5A5';
          fbErr.innerHTML = `<strong>Dispatch Error:</strong> ${err.message}`;
          fbErr.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
        btn.disabled = false;
        btn.innerText = 'Enqueue Task';
      }
    }

    function pollTaskResult(taskId) {
      let attempts = 0;
      const interval = setInterval(async () => {
        attempts++;
        await fetchTasks();
        const t = allTasks.find(x => x.id === taskId);
        if (t) {
          const statusEl = document.getElementById(`fb-status-${taskId}`);
          const resEl = document.getElementById(`fb-result-${taskId}`);
          if (statusEl) statusEl.innerText = t.status;
          if (t.status === 'SUCCESS') {
            if (resEl) {
              resEl.style.color = '#34D399';
              resEl.innerHTML = `<strong>Result Returned:</strong> <code>${JSON.stringify(t.result || { status: 'SUCCESS' })}</code>`;
            }
            showToast(
              'Task Finished: ' + t.title,
              `Execution returned: ${JSON.stringify(t.result || {})}`,
              'success',
              { label: 'Inspect Result', onClick: () => openTaskModal(taskId) }
            );
            clearInterval(interval);
          } else if (t.status === 'FAILED' || t.status === 'DEAD_LETTERED') {
            if (resEl) {
              resEl.style.color = '#F87171';
              resEl.innerHTML = `<strong>Execution Failed:</strong> ${t.error_message || 'Task failed'}`;
            }
            showToast(
              'Task Failed: ' + t.title,
              t.error_message || 'Task execution error',
              'error',
              { label: 'Inspect Error', onClick: () => openTaskModal(taskId) }
            );
            clearInterval(interval);
          } else if (t.status === 'RUNNING') {
            if (resEl) {
              resEl.style.color = '#38BDF8';
              resEl.innerHTML = `Worker executing task (${t.progress || 25}%)...`;
            }
          }
        }
        if (attempts >= 30) clearInterval(interval);
      }, 350);
    }

    async function replayAllDLQ() {
      const btn = document.getElementById('replay-all-btn');
      const originalText = btn ? btn.innerText : 'Replay All';
      if (btn) {
        btn.disabled = true;
        btn.innerText = 'Replaying...';
      }
      try {
        let token = await ensureAuth();
        let res = await fetch(getApiUrl('/api/v1/tasks/dlq/replay-all'), {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (res.status === 401) {
          token = await ensureAuth(true);
          res = await fetch(getApiUrl('/api/v1/tasks/dlq/replay-all'), {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${token}` }
          });
        }
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          if (btn) btn.innerText = 'Replayed';
          setTimeout(() => { if (btn) { btn.disabled = false; btn.innerText = originalText; } }, 1500);
          fetchTasks();
        } else {
          alert(data.detail || data.message || 'DLQ Replay failed');
          if (btn) { btn.disabled = false; btn.innerText = originalText; }
        }
      } catch (err) {
        alert('DLQ Replay error: ' + err.message);
        if (btn) { btn.disabled = false; btn.innerText = originalText; }
      }
    }
    let lastExportedCsv = '';

    async function exportTasksCsv() {
      let token = await ensureAuth();
      const btn = document.getElementById('export-btn');
      const originalText = btn ? btn.innerText : 'Export CSV';
      if (btn) {
        btn.disabled = true;
        btn.innerText = 'Exporting...';
      }
      let csvContent = '';
      let taskCount = 0;

      try {
        let res = await fetch(getApiUrl('/api/v1/tasks/export?format=csv'), {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (res.status === 401) {
          token = await ensureAuth(true);
          res = await fetch(getApiUrl('/api/v1/tasks/export?format=csv'), {
            headers: { 'Authorization': `Bearer ${token}` }
          });
        }
        if (res.ok) {
          csvContent = await res.text();
          const lines = csvContent.trim().split(String.fromCharCode(10)).filter(l => l.trim().length > 0);
          taskCount = Math.max(0, lines.length - 1);
        } else {
          const clientData = buildClientSideCsv();
          csvContent = clientData.content;
          taskCount = clientData.count;
        }
      } catch (err) {
        console.warn('Backend export fallback:', err);
        const clientData = buildClientSideCsv();
        csvContent = clientData.content;
        taskCount = clientData.count;
      }

      if (!csvContent || csvContent.trim().length === 0) {
        const clientData = buildClientSideCsv();
        csvContent = clientData.content;
        taskCount = clientData.count;
      }

      lastExportedCsv = csvContent;
      if (btn) {
        btn.innerText = `Exported (${taskCount})`;
        setTimeout(() => {
          btn.disabled = false;
          btn.innerText = originalText;
        }, 2000);
      }

      // 1. Display CSV data in modal preview immediately
      showCsvExportModal(csvContent, taskCount);

      // 2. Trigger browser file download
      triggerCsvDownload();

      // 3. Unmistakable toast notification
      showToast(
        'CSV Audit Export Ready',
        `Generated export with ${taskCount} task records. File download initiated.`,
        'success',
        { label: 'View CSV Stream', onClick: () => showCsvExportModal(lastExportedCsv, taskCount) }
      );
    }

    function buildClientSideCsv() {
      const headers = ["task_id", "title", "task_type", "status", "priority", "current_attempt", "max_retries", "trace_id", "delay_seconds", "created_at"];
      const rows = (allTasks || []).map(t => [
        t.id,
        `"${(t.title || '').replace(/"/g, '""')}"`,
        t.task_type || '',
        t.status || '',
        t.priority || 5,
        t.current_attempt || 0,
        t.max_retries || 4,
        t.trace_id || '',
        t.delay_seconds || 0,
        t.created_at || ''
      ]);
      const content = [headers.join(','), ...rows.map(r => r.join(','))].join(String.fromCharCode(10));
      return { content: content, count: (allTasks || []).length };
    }

    function triggerCsvDownload() {
      if (!lastExportedCsv) return;
      try {
        const filename = `cloudtask_audit_${Date.now()}.csv`;
        const blob = new Blob([lastExportedCsv], { type: 'text/csv;charset=utf-8;' });
        if (window.navigator && window.navigator.msSaveOrOpenBlob) {
          window.navigator.msSaveOrOpenBlob(blob, filename);
          return;
        }
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.setAttribute('download', filename);
        a.style.display = 'none';
        document.body.appendChild(a);
        a.click();
        setTimeout(() => {
          try {
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
          } catch (e) {}
        }, 300);
      } catch (e) {
        console.warn('Direct file download fallback:', e);
        try {
          const encodedUri = 'data:text/csv;charset=utf-8,' + encodeURIComponent(lastExportedCsv);
          const link = document.createElement('a');
          link.setAttribute('href', encodedUri);
          link.setAttribute('download', `cloudtask_audit_${Date.now()}.csv`);
          document.body.appendChild(link);
          link.click();
          setTimeout(() => { try { document.body.removeChild(link); } catch (e) {} }, 300);
        } catch (e2) {
          console.warn('Data URI download fallback:', e2);
        }
      }
    }

    function showCsvExportModal(csvText, count) {
      const modal = document.getElementById('csv-export-modal');
      const preview = document.getElementById('csv-export-preview');
      const countEl = document.getElementById('csv-export-count');
      if (countEl) countEl.innerText = count !== undefined ? count : (csvText ? Math.max(0, csvText.trim().split(String.fromCharCode(10)).length - 1) : 0);
      if (preview) preview.innerText = csvText || '(No tasks recorded)';
      if (modal) {
        modal.style.display = 'flex';
      }
    }

    function closeCsvExportModal() {
      const modal = document.getElementById('csv-export-modal');
      if (modal) modal.style.display = 'none';
    }

    function handleCsvBackdropClick(e) {
      if (e.target.id === 'csv-export-modal') closeCsvExportModal();
    }

    function copyCsvPreview(btn) {
      if (lastExportedCsv && navigator.clipboard) {
        navigator.clipboard.writeText(lastExportedCsv);
        btn.innerText = 'Copied';
        setTimeout(() => btn.innerText = 'Copy to Clipboard', 1500);
      }
    }

    async function cancelTask(taskId) {
      let token = await ensureAuth();
      let res = await fetch(getApiUrl(`/api/v1/tasks/${taskId}/cancel`), {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (res.status === 401) {
        token = await ensureAuth(true);
        await fetch(getApiUrl(`/api/v1/tasks/${taskId}/cancel`), {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}` }
        });
      }
      fetchTasks();
    }

    async function retryTask(taskId) {
      let token = await ensureAuth();
      let res = await fetch(getApiUrl(`/api/v1/tasks/${taskId}/retry`), {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (res.status === 401) {
        token = await ensureAuth(true);
        await fetch(getApiUrl(`/api/v1/tasks/${taskId}/retry`), {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}` }
        });
      }
      fetchTasks();
    }

    let currentExecutionMode = 'auto';
    let isProcessingQueue = false;

    async function fetchExecutionMode() {
      try {
        let res = await fetch(getApiUrl('/api/v1/tasks/execution-mode'));
        if (res.ok) {
          const data = await res.json();
          currentExecutionMode = data.mode || 'auto';
          updateModeUI();
        }
      } catch (err) {
        console.warn('Execution mode check:', err);
      }
    }

    function updateModeUI() {
      const badge = document.getElementById('mode-badge');
      const guidance = document.getElementById('mode-guidance-text');
      const btn = document.getElementById('mode-toggle-btn');
      if (currentExecutionMode === 'auto') {
        if (badge) {
          badge.innerText = 'Auto-Start';
          badge.style.color = '#34D399';
        }
        if (btn) {
          btn.style.borderColor = '#10B981';
          btn.style.color = '#6EE7B7';
        }
        if (guidance) {
          guidance.innerHTML = '<strong>Auto-Start Mode:</strong> Tasks execute automatically in background as soon as they are submitted.';
        }
      } else {
        if (badge) {
          badge.innerText = 'Manual (Staged)';
          badge.style.color = '#38BDF8';
        }
        if (btn) {
          btn.style.borderColor = '#3B82F6';
          btn.style.color = '#93C5FD';
        }
        if (guidance) {
          guidance.innerHTML = '<strong>Manual Batch Mode:</strong> Enter multiple tasks below with different priorities. They wait in queue until you tap <strong>Start Processing</strong> to execute in priority order (P10 to P1).';
        }
      }
    }

    async function toggleExecutionMode() {
      const targetMode = currentExecutionMode === 'manual' ? 'auto' : 'manual';
      const btn = document.getElementById('mode-toggle-btn');
      if (btn) btn.disabled = true;
      try {
        let res = await fetch(getApiUrl('/api/v1/tasks/execution-mode'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: targetMode })
        });
        if (res.ok) {
          const data = await res.json();
          currentExecutionMode = data.mode || targetMode;
          updateModeUI();
          if (currentExecutionMode === 'auto') {
            const queued = data.queued_count || parseInt(document.getElementById('queued-count').innerText || '0');
            showToast(
              'Mode Switched: Auto-Start Active',
              `Tasks execute automatically in background as soon as submitted.${queued > 0 ? ' Auto-processing ' + queued + ' queued tasks.' : ''}`,
              'success',
              queued > 0 ? { label: `Watch Processing (${queued})`, onClick: () => startPriorityProcessing() } : null
            );
            if (queued > 0) fetchTasks();
          } else {
            showToast(
              'Mode Switched: Manual Batch Mode',
              'Tasks will stage in the QUEUED column so you can test priority sorting. Click "Start Processing" when you are ready to execute.',
              'info',
              { label: 'Prefill Sample Task', onClick: () => toggleDispatcher() }
            );
          }
        } else {
          showToast('Mode Switch Failed', 'Backend returned an error updating mode.', 'error');
        }
      } catch (err) {
        console.error('Toggle execution mode error:', err);
        showToast('Mode Switch Error', err.message, 'error');
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    async function clearHistory() {
      const btn = document.getElementById('clear-history-btn');
      const originalText = btn ? btn.innerText : 'Clear History';
      const successCount = parseInt(document.getElementById('success-count').innerText || '0');
      if (successCount === 0) {
        showToast('Clear History', 'No completed tasks in history to clear.', 'info');
        return;
      }
      if (!confirm(`Are you sure you want to clear ${successCount} completed tasks from history?`)) {
        return;
      }
      if (btn) {
        btn.disabled = true;
        btn.innerText = 'Clearing...';
      }
      try {
        let token = await ensureAuth();
        let res = await fetch(getApiUrl('/api/v1/tasks/clear-history'), {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
          }
        });
        if (res.status === 401) {
          token = await ensureAuth(true);
          res = await fetch(getApiUrl('/api/v1/tasks/clear-history'), {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Authorization': `Bearer ${token}`
            }
          });
        }
        if (res.ok) {
          if (btn) btn.innerText = 'Cleared';
          showToast('History Cleared', `Successfully purged ${successCount} completed tasks.`, 'success');
          await fetchTasks();
          setTimeout(() => {
            if (btn) {
              btn.disabled = false;
              btn.innerText = originalText;
            }
          }, 1500);
        } else {
          showToast('Clear Failed', 'Failed to clear task history from database.', 'error');
          if (btn) {
            btn.disabled = false;
            btn.innerText = originalText;
          }
        }
      } catch (err) {
        console.error('Clear history error:', err);
        showToast('Clear History Error', err.message, 'error');
        if (btn) {
          btn.disabled = false;
          btn.innerText = originalText;
        }
      }
    }

    async function seedAndProcessDemoTasks() {
      showToast('Seeding Demo Tasks', 'Submitting 3 sample tasks with priorities P10 (Critical), P8 (High), and P5 (Normal)...', 'info');
      const demoTasks = [
        { title: 'Critical Financial Audit Report', task_type: 'report_generation', priority: 10 },
        { title: 'High-Volume ETL Ingestion Batch', task_type: 'data_processing', priority: 8 },
        { title: 'Customer Notification Dispatch', task_type: 'email_dispatch', priority: 5 }
      ];
      let token = await ensureAuth();
      for (const dt of demoTasks) {
        try {
          await fetch(getApiUrl('/api/v1/tasks'), {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({
              title: dt.title,
              task_type: dt.task_type,
              priority: dt.priority,
              delay_seconds: 0,
              prevent_duplicates: false,
              payload: { demo: true, timestamp: Date.now() }
            })
          });
        } catch (e) {
          console.warn('Demo task seed error:', e);
        }
      }
      await fetchTasks();
      setTimeout(() => startPriorityProcessing(), 400);
    }

    async function startPriorityProcessing() {
      if (isProcessingQueue) return;

      const btn = document.getElementById('start-btn');
      const colBtn = document.getElementById('col-start-btn');
      const queuedCount = parseInt(document.getElementById('queued-count').innerText || '0');

      if (queuedCount === 0) {
        showToast(
          'Queue Empty (0 Staged Tasks)',
          'There are no tasks waiting in the queue. Click below to automatically dispatch and process 3 demo tasks with priorities P10, P8, and P5.',
          'warning',
          { label: 'Dispatch & Process 3 Demo Tasks', onClick: () => seedAndProcessDemoTasks() }
        );
        return;
      }

      isProcessingQueue = true;
      if (btn) {
        btn.disabled = true;
        btn.innerHTML = 'Processing (P10 to P1)...';
        btn.style.background = '#F59E0B';
      }
      if (colBtn) {
        colBtn.disabled = true;
        colBtn.innerText = 'Processing...';
      }

      showToast(
        'Priority Processing Initiated',
        `Processing ${queuedCount} queued tasks strictly in descending priority order (P10 to P1)...`,
        'info'
      );

      try {
        await fetch(getApiUrl('/api/v1/tasks/start-processing'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' }
        });

        // Fast rapid polling at 350ms to visibly capture and animate transitions: QUEUED -> RUNNING -> SUCCESS
        let polls = 0;
        const maxPolls = 70; // 70 * 350ms = ~24.5s max
        const fastPoll = setInterval(async () => {
          await fetchTasks();
          polls++;
          const remainingQueued = parseInt(document.getElementById('queued-count').innerText || '0');
          const runningCount = parseInt(document.getElementById('running-count').innerText || '0');
          if ((remainingQueued === 0 && runningCount === 0) || polls >= maxPolls) {
            clearInterval(fastPoll);
            isProcessingQueue = false;
            if (btn) {
              btn.disabled = false;
              btn.innerHTML = 'Start Processing (<span id="start-count">' + remainingQueued + '</span>)';
              btn.style.background = '#10B981';
            }
            if (colBtn) {
              colBtn.disabled = false;
              colBtn.innerText = 'Process Queue';
            }
            showToast(
              'Priority Batch Complete',
              'All staged tasks have executed in priority order with live output data returned.',
              'success',
              { label: 'Export Audit CSV', onClick: () => exportTasksCsv() }
            );
          }
        }, 350);
      } catch (err) {
        console.error('Failed to start processing:', err);
        isProcessingQueue = false;
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = 'Start Processing (<span id="start-count">' + queuedCount + '</span>)';
          btn.style.background = '#10B981';
        }
        if (colBtn) {
          colBtn.disabled = false;
          colBtn.innerText = 'Process Queue';
        }
        showToast('Processing Error', err.message, 'error');
      }
    }

    // Dynamic endpoint detection and live polling initialization
    detectApiBase().then(() => {
      fetchExecutionMode();
      fetchTasks();
      setInterval(fetchTasks, 2500);
    });
  </script>
</body>
</html>"""

# Health and Metrics
@app.get("/health/live", tags=["Health"])
async def liveness():
    return {
        "status": "UP",
        "service": "api-gateway",
        "version": "1.2.3",
        "auth_loaded": auth_loaded,
        "task_loaded": task_loaded,
        "load_errors": load_errors,
    }


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
async def proxy_request(service_name: str, target_base_url: str, request: Request, subpath: str = ""):
    start_time = time.time()
    clean_subpath = subpath.lstrip("/")
    url = f"{target_base_url}/{clean_subpath}" if clean_subpath else target_base_url

    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers = dict(request.headers)
    headers.pop("host", None)
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    headers["X-Correlation-ID"] = correlation_id

    body = await request.body()

    upstream_resp = None
    for attempt in range(3):
        try:
            upstream_resp = await http_client.request(
                method=request.method,
                url=url,
                headers=headers,
                content=body,
            )
            break
        except httpx.RequestError as exc:
            if attempt < 2:
                await asyncio.sleep(0.3)
                continue
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


# In-process routers (with fallback to reverse proxy)
def load_service_router(service_dir_name: str):
    import importlib.util
    root_dir = Path(__file__).resolve().parent.parent
    file_path = root_dir / service_dir_name / "routes.py"
    pkg_name = f"services.{service_dir_name}"
    mod_name = f"services.{service_dir_name}.routes"
    spec = importlib.util.spec_from_file_location(
        mod_name,
        file_path,
        submodule_search_locations=[str(root_dir / service_dir_name)]
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = pkg_name
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module.router


try:
    auth_router = load_service_router("auth-service")
    app.include_router(auth_router, prefix="/api/v1")
    auth_loaded = True
    logger.info("Direct in-process Auth router registered successfully.")
except Exception as e:
    load_errors["auth"] = f"{type(e).__name__}: {str(e)}"
    logger.warning(f"Could not load in-process Auth router ({e}). Using reverse proxy routes.")

try:
    task_router = load_service_router("task-service")
    app.include_router(task_router, prefix="/api/v1")
    task_loaded = True
    logger.info("Direct in-process Task router registered successfully.")
except Exception as e:
    load_errors["task"] = f"{type(e).__name__}: {str(e)}"
    logger.warning(f"Could not load in-process Task router ({e}). Using reverse proxy routes.")

if not auth_loaded:
    # Auth Service Proxy Routes (/api/v1/auth/...)
    @app.api_route("/api/v1/auth/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def auth_proxy(path: str, request: Request):
        return await proxy_request("auth-service", f"{AUTH_SERVICE_URL}/auth", request, path)

if not task_loaded:
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
