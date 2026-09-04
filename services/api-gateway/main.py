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
      <a href="/dashboard" class="btn btn-primary" style="background:#10B981;box-shadow:0 4px 14px rgba(16,185,129,0.35);">📊 Real-Time Task Dashboard</a>
      <a href="/docs" class="btn btn-primary">📖 Swagger UI (/docs)</a>
      <button onclick="runLiveHealthCheck()" class="btn btn-secondary" id="health-btn">💚 Live Health Check</button>
    </div>

    <div id="health-panel">
      <h4>
        <span>🟢 Live Diagnostics Result</span>
        <button type="button" onclick="copyHealthJson()" id="copy-btn" style="background:#1E293B;color:#94A3B8;border:1px solid #334155;border-radius:4px;padding:4px 10px;font-size:0.75rem;cursor:pointer;font-weight:500;">📋 Copy JSON</button>
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
    <div style="background:#0B0F19;border-bottom:1px solid #1F2937;padding:10px 16px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:99999;font-family:system-ui, -apple-system, sans-serif;flex-wrap:wrap;gap:8px;">
      <div style="display:flex;align-items:center;gap:8px;">
        <span style="font-weight:700;color:#FFFFFF;font-size:0.95rem;letter-spacing:-0.02em;">⚡ CloudTask API Explorer</span>
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
    .kanban-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; align-items: flex-start; }
    .kanban-col { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: var(--radius); padding: 14px; display: flex; flex-direction: column; min-height: 380px; }
    .col-header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 10px; border-bottom: 1px solid var(--card-border); margin-bottom: 12px; font-weight: 700; font-size: 0.88rem; }

    /* Task Card */
    .task-card { background: var(--card-inner); border: 1px solid var(--card-border); border-radius: 8px; padding: 12px; margin-bottom: 10px; transition: all 0.2s; cursor: pointer; }
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
      .kanban-grid { grid-template-columns: 1fr; }
      .kanban-col { min-height: auto; }
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
  </style>
</head>
<body>
  <div class="container">
    <!-- Navbar -->
    <div class="nav">
      <div class="nav-brand">
        <span>⚡ CloudTask Engine</span>
        <div class="status-pill">
          <div class="pulse-dot"></div>
          <span>Active</span>
        </div>
      </div>
      <div class="nav-links">
        <a href="/" class="btn btn-secondary">← Portal</a>
        <a href="/docs" class="btn btn-secondary">📖 API Docs</a>
        <button onclick="exportTasksCsv()" class="btn btn-secondary" id="export-btn">📥 Export CSV</button>
        <button onclick="toggleDispatcher()" class="btn btn-primary" id="toggle-dispatch-btn">➕ New Task</button>
        <button onclick="fetchTasks()" class="btn btn-secondary" id="refresh-btn">🔄</button>
      </div>
    </div>

    <!-- Cluster Fleet Summary Banner -->
    <div class="cluster-banner">
      <div><strong>Cluster Nodes:</strong> 2 Distributed Workers Active | <strong>Broker:</strong> RabbitMQ Priority Queue | <strong>Mutex:</strong> Redis Leases</div>
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
        <span>🔍</span>
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
      </div>
    </div>

    <!-- Quick Dispatcher Form (Collapsible) -->
    <div class="dispatch-box" id="dispatch-panel" style="display: none;">
      <h3>
        <span>🚀 Dispatch Asynchronous Task to Cluster</span>
        <button type="button" onclick="toggleDispatcher()" style="background:none;border:none;color:#94A3B8;cursor:pointer;font-size:0.9rem;">✕ Close</button>
      </h3>
      <form id="task-form" onsubmit="handleDispatch(event)">
        <div class="form-grid">
          <div class="form-group">
            <label>Task Title</label>
            <input type="text" id="task-title" class="form-control" placeholder="e.g. Ingest Customer Ledger" required />
          </div>
          <div class="form-group">
            <label>Handler Type</label>
            <select id="task-type" class="form-control">
              <option value="report_generation">report_generation (PDF Export)</option>
              <option value="data_processing">data_processing (ETL Batch)</option>
              <option value="email_dispatch">email_dispatch (Transactional Email)</option>
              <option value="system_cleanup">system_cleanup (Purge Temp Records)</option>
            </select>
          </div>
          <div class="form-group">
            <label>Priority</label>
            <select id="task-priority" class="form-control">
              <option value="10">10 - Critical Priority 🔥</option>
              <option value="8" selected>8 - High Priority ⚡</option>
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
            <input type="url" id="task-webhook" class="form-control" placeholder="https://api.example.com/webhook (Optional)" />
          </div>
          <div class="form-group" style="align-self: flex-end;">
            <button type="submit" class="btn btn-success" style="width: 100%; padding: 10px; justify-content: center;" id="submit-btn">
              ⚡ Enqueue Task
            </button>
          </div>
        </div>
      </form>
    </div>

    <!-- Mobile Segmented Tabs -->
    <div class="mobile-tabs" id="mobile-tabs">
      <button class="tab-pill active" onclick="setMobileView('ALL')">All (<span id="m-all">0</span>)</button>
      <button class="tab-pill" onclick="setMobileView('QUEUED')">🟡 Queued (<span id="m-queued">0</span>)</button>
      <button class="tab-pill" onclick="setMobileView('RUNNING')">🔵 Running (<span id="m-running">0</span>)</button>
      <button class="tab-pill" onclick="setMobileView('SUCCESS')">🟢 Success (<span id="m-success">0</span>)</button>
      <button class="tab-pill" onclick="setMobileView('DEAD_LETTERED')">🔴 DLQ (<span id="m-dlq">0</span>)</button>
    </div>

    <!-- Kanban Grid -->
    <div class="kanban-grid">
      <div class="kanban-col" id="col-container-queued">
        <div class="col-header">
          <span>🟡 QUEUED / PENDING</span>
          <span id="badge-queued" class="tag">0</span>
        </div>
        <div id="col-queued"></div>
      </div>
      <div class="kanban-col" id="col-container-running">
        <div class="col-header">
          <span>🔵 RUNNING (Worker)</span>
          <span id="badge-running" class="tag">0</span>
        </div>
        <div id="col-running"></div>
      </div>
      <div class="kanban-col" id="col-container-success">
        <div class="col-header">
          <span>🟢 COMPLETED</span>
          <span id="badge-success" class="tag">0</span>
        </div>
        <div id="col-success"></div>
      </div>
      <div class="kanban-col" id="col-container-dlq">
        <div class="col-header">
          <div style="display: flex; align-items: center; gap: 6px;">
            <span>🔴 DEAD_LETTERED / FAILED</span>
            <span id="badge-dlq" class="tag">0</span>
          </div>
          <button id="replay-all-btn" onclick="replayAllDLQ()" class="btn btn-danger btn-xs" title="Replay all DLQ tasks" style="padding: 2px 7px;">Replay All</button>
        </div>
        <div id="col-dlq"></div>
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
        <button type="button" class="modal-close" onclick="closeTaskModal()">✕</button>
      </div>
      <div class="modal-body">
        <!-- Metadata Grid -->
        <div class="detail-grid">
          <div class="detail-item">
            <div class="detail-label">Task ID</div>
            <div class="detail-val">
              <span id="modal-task-id" style="font-size:0.75rem;">-</span>
              <button onclick="copyModalField('modal-task-id', this)" class="btn-xs btn-secondary" style="margin-left:6px;">📋 Copy</button>
            </div>
          </div>
          <div class="detail-item">
            <div class="detail-label">Distributed Trace ID</div>
            <div class="detail-val">
              <span id="modal-trace-id" style="font-size:0.75rem;">-</span>
              <button onclick="copyModalField('modal-trace-id', this)" class="btn-xs btn-secondary" style="margin-left:6px;">📋 Copy</button>
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

  <script>
    let authToken = localStorage.getItem('cloudtask_token') || '';
    let allTasks = [];
    let currentSearch = '';
    let currentTypeFilter = '';
    let currentPriorityFilter = '';
    let activeMobileTab = 'ALL';
    let currentModalTaskId = null;

    async function ensureAuth(forceRefresh = false) {
      if (authToken && !forceRefresh) return authToken;
      try {
        const res = await fetch('/api/v1/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: 'admin@cloudtask.dev', password: 'AdminSecurePass123!' })
        });
        if (res.ok) {
          const data = await res.json();
          authToken = data.access_token;
          localStorage.setItem('cloudtask_token', authToken);
          return authToken;
        } else {
          localStorage.removeItem('cloudtask_token');
          authToken = '';
        }
      } catch (err) {
        console.warn('Auto-login error:', err);
      }
      return authToken;
    }

    async function fetchTasks() {
      let token = await ensureAuth();
      if (!token) return;

      try {
        let res = await fetch('/api/v1/tasks?limit=100', {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (res.status === 401) {
          token = await ensureAuth(true);
          if (token) {
            res = await fetch('/api/v1/tasks?limit=100', {
              headers: { 'Authorization': `Bearer ${token}` }
            });
          }
        }
        if (!res.ok) return;

        const data = await res.json();
        allTasks = data.tasks || [];
        applyFiltersAndRender();
      } catch (err) {
        console.error('Fetch error:', err);
      }
    }

    function toggleDispatcher() {
      const panel = document.getElementById('dispatch-panel');
      const btn = document.getElementById('toggle-dispatch-btn');
      if (panel.style.display === 'none') {
        panel.style.display = 'block';
        btn.innerText = '➖ Close Form';
      } else {
        panel.style.display = 'none';
        btn.innerText = '➕ New Task';
      }
    }

    function handleSearch(val) {
      currentSearch = val.toLowerCase().trim();
      applyFiltersAndRender();
    }

    function applyFilters() {
      currentTypeFilter = document.getElementById('filter-type').value;
      currentPriorityFilter = document.getElementById('filter-priority').value;
      applyFiltersAndRender();
    }

    function setMobileView(tab) {
      activeMobileTab = tab;
      const pills = document.querySelectorAll('.tab-pill');
      pills.forEach(p => p.classList.remove('active'));
      event.target.classList.add('active');

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
      const filtered = allTasks.filter(task => {
        if (currentTypeFilter && task.task_type !== currentTypeFilter) return false;
        if (currentPriorityFilter && String(task.priority) !== currentPriorityFilter) return false;
        if (currentSearch) {
          const matchTitle = (task.title || '').toLowerCase().includes(currentSearch);
          const matchId = (task.id || '').toLowerCase().includes(currentSearch);
          const matchTrace = (task.trace_id || '').toLowerCase().includes(currentSearch);
          if (!matchTitle && !matchId && !matchTrace) return false;
        }
        return true;
      });

      renderColumns(filtered);
    }

    function renderColumns(tasks) {
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
          delayBadge = `<span class="tag tag-delay">⏳ ${task.delay_seconds}s</span>`;
        }
        let webhookBadge = '';
        if (task.webhook_url) {
          webhookBadge = `<span class="tag tag-wh" title="${task.webhook_url}">🔗 Hook</span>`;
        }

        let actionHtml = `
          <div class="card-actions">
            <button onclick="openTaskModal('${task.id}')" class="btn btn-secondary btn-xs">🔍 Inspect</button>
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

      // Update counters
      document.getElementById('total-count').innerText = counts.total;
      document.getElementById('queued-count').innerText = counts.queued;
      document.getElementById('running-count').innerText = counts.running;
      document.getElementById('success-count').innerText = counts.success;
      document.getElementById('dlq-count').innerText = counts.dlq;

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
      if (e.key === 'Escape') closeTaskModal();
    });

    function copyModalField(elementId, btn) {
      const text = document.getElementById(elementId).innerText;
      if (navigator.clipboard) {
        navigator.clipboard.writeText(text);
        btn.innerText = '✅ Copied';
        setTimeout(() => btn.innerText = '📋 Copy', 1500);
      }
    }

    async function handleDispatch(e) {
      e.preventDefault();
      const btn = document.getElementById('submit-btn');
      btn.disabled = true;
      btn.innerText = '⏳ Dispatching...';

      let token = await ensureAuth();
      let title = document.getElementById('task-title').value.trim();
      if (!title) {
        title = 'Quick Task #' + Date.now().toString().slice(-4);
      }
      const task_type = document.getElementById('task-type').value;
      const priority = parseInt(document.getElementById('task-priority').value, 10);
      const delay = parseInt(document.getElementById('task-delay').value, 10) || 0;
      const webhook = document.getElementById('task-webhook').value.trim() || null;

      try {
        const payloadBody = {
          title: title,
          task_type: task_type,
          priority: priority,
          delay_seconds: delay,
          payload: { timestamp: Date.now() }
        };
        if (webhook) payloadBody.webhook_url = webhook;

        let res = await fetch('/api/v1/tasks', {
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
          res = await fetch('/api/v1/tasks', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify(payloadBody)
          });
        }

        if (res.ok) {
          document.getElementById('task-title').value = '';
          document.getElementById('task-webhook').value = '';
          document.getElementById('task-delay').value = '0';
          btn.innerText = '✅ Enqueued!';
          setTimeout(() => {
            btn.disabled = false;
            btn.innerText = '⚡ Enqueue Task';
          }, 1500);
          fetchTasks();
        } else {
          const errData = await res.json().catch(() => ({}));
          alert('Task dispatch failed (' + res.status + '): ' + (errData.detail || 'Service temporarily unavailable. Please retry.'));
          btn.disabled = false;
          btn.innerText = '⚡ Enqueue Task';
        }
      } catch (err) {
        alert('Dispatch error: ' + err.message);
        btn.disabled = false;
        btn.innerText = '⚡ Enqueue Task';
      }
    }

    async function replayAllDLQ() {
      const btn = document.getElementById('replay-all-btn');
      const originalText = btn ? btn.innerText : 'Replay All';
      if (btn) {
        btn.disabled = true;
        btn.innerText = '⏳ Replaying...';
      }
      try {
        let token = await ensureAuth();
        let res = await fetch('/api/v1/tasks/dlq/replay-all', {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (res.status === 401) {
          token = await ensureAuth(true);
          res = await fetch('/api/v1/tasks/dlq/replay-all', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${token}` }
          });
        }
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          if (btn) btn.innerText = '✅ Replayed!';
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

    async function exportTasksCsv() {
      let token = await ensureAuth();
      const btn = document.getElementById('export-btn');
      btn.innerText = '⏳ Exporting...';
      try {
        let res = await fetch('/api/v1/tasks/export?format=csv', {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (res.status === 401) {
          token = await ensureAuth(true);
          res = await fetch('/api/v1/tasks/export?format=csv', {
            headers: { 'Authorization': `Bearer ${token}` }
          });
        }
        if (res.ok) {
          const blob = await res.blob();
          const url = window.URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `cloudtask_audit_${Date.now()}.csv`;
          document.body.appendChild(a);
          a.click();
          a.remove();
          btn.innerText = '✅ Exported!';
        } else {
          exportClientSideCsv();
          btn.innerText = '✅ Exported!';
        }
      } catch (err) {
        exportClientSideCsv();
        btn.innerText = '✅ Exported!';
      }
      setTimeout(() => btn.innerText = '📥 Export CSV', 2000);
    }

    function exportClientSideCsv() {
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
      const csvContent = [headers.join(','), ...rows.map(r => r.join(','))].join('\n');
      const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `cloudtask_audit_${Date.now()}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
    }

    async function cancelTask(taskId) {
      let token = await ensureAuth();
      let res = await fetch(`/api/v1/tasks/${taskId}/cancel`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (res.status === 401) {
        token = await ensureAuth(true);
        await fetch(`/api/v1/tasks/${taskId}/cancel`, {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}` }
        });
      }
      fetchTasks();
    }

    async function retryTask(taskId) {
      let token = await ensureAuth();
      let res = await fetch(`/api/v1/tasks/${taskId}/retry`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (res.status === 401) {
        token = await ensureAuth(true);
        await fetch(`/api/v1/tasks/${taskId}/retry`, {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}` }
        });
      }
      fetchTasks();
    }

    // Initial fetch and 2.5-second live polling loop
    fetchTasks();
    setInterval(fetchTasks, 2500);
  </script>
</body>
</html>"""

# Health and Metrics
@app.get("/health/live", tags=["Health"])
async def liveness():
    return {
        "status": "UP",
        "service": "api-gateway",
        "version": "1.2.2",
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
