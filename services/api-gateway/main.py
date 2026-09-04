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


@app.get("/dashboard", response_class=HTMLResponse, tags=["Dashboard"])
async def real_time_dashboard():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>CloudTask - Real-Time Task Dashboard</title>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap">
  <style>
    :root {
      --bg: #0B0F19;
      --card-bg: #111827;
      --card-border: #1F2937;
      --primary: #2563EB;
      --success: #10B981;
      --warning: #F59E0B;
      --danger: #EF4444;
      --text: #F3F4F6;
      --text-muted: #9CA3AF;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', sans-serif; }
    body { background-color: var(--bg); color: var(--text); padding: 24px 16px; min-height: 100vh; }
    .container { max-width: 1280px; margin: 0 auto; }
    .nav { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--card-border); padding-bottom: 16px; margin-bottom: 24px; flex-wrap: wrap; gap: 12px; }
    .nav-brand { display: flex; align-items: center; gap: 10px; font-weight: 800; font-size: 1.25rem; color: #FFFFFF; }
    .nav-links { display: flex; align-items: center; gap: 12px; }
    .btn { padding: 8px 16px; border-radius: 6px; font-size: 0.85rem; font-weight: 600; text-decoration: none; border: none; cursor: pointer; transition: all 0.2s; display: inline-flex; align-items: center; gap: 6px; }
    .btn-primary { background: var(--primary); color: #fff; }
    .btn-success { background: var(--success); color: #fff; }
    .btn-danger { background: var(--danger); color: #fff; }
    .btn-secondary { background: var(--card-bg); color: var(--text); border: 1px solid var(--card-border); }
    .metrics-bar { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
    .metric-card { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 10px; padding: 18px; }
    .metric-label { font-size: 0.8rem; color: var(--text-muted); text-transform: uppercase; font-weight: 600; }
    .metric-val { font-size: 1.8rem; font-weight: 800; margin-top: 6px; }
    .dispatch-box { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 10px; padding: 20px; margin-bottom: 24px; }
    .dispatch-box h3 { font-size: 1.1rem; margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }
    .form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; }
    .form-group { display: flex; flex-direction: column; gap: 6px; }
    .form-group label { font-size: 0.8rem; color: var(--text-muted); font-weight: 500; }
    .form-control { background: #070B14; border: 1px solid var(--card-border); border-radius: 6px; padding: 9px 12px; color: #fff; font-size: 0.9rem; }
    .kanban-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 18px; }
    .kanban-col { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 10px; padding: 16px; display: flex; flex-direction: column; min-height: 420px; }
    .col-header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 12px; border-bottom: 1px solid var(--card-border); margin-bottom: 14px; font-weight: 700; font-size: 0.95rem; }
    .task-card { background: #0D1527; border: 1px solid #1E293B; border-radius: 8px; padding: 14px; margin-bottom: 12px; transition: transform 0.2s; }
    .task-card:hover { transform: translateY(-2px); border-color: #3B82F6; }
    .task-title { font-weight: 600; font-size: 0.95rem; color: #FFFFFF; margin-bottom: 6px; }
    .task-meta { font-size: 0.75rem; color: var(--text-muted); display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
    .tag { background: #1E293B; padding: 2px 6px; border-radius: 4px; font-weight: 600; }
    .tag-prio { background: rgba(245, 158, 11, 0.2); color: #FBBF24; }
    .progress-bar { width: 100%; height: 6px; background: #1F2937; border-radius: 9999px; overflow: hidden; margin: 8px 0; }
    .progress-fill { height: 100%; background: linear-gradient(90deg, #3B82F6, #10B981); transition: width 0.4s; }
    .card-actions { display: flex; gap: 8px; margin-top: 10px; }
    .btn-sm { padding: 4px 10px; font-size: 0.75rem; border-radius: 4px; }
  </style>
</head>
<body>
  <div class="container">
    <div class="nav">
      <div class="nav-brand">
        <span>⚡ CloudTask Live Engine</span>
        <span style="font-size: 0.75rem; background: rgba(16, 185, 129, 0.2); color: #34D399; padding: 3px 8px; border-radius: 9999px;">Cluster Active</span>
      </div>
      <div class="nav-links">
        <a href="/" class="btn btn-secondary">← Home Portal</a>
        <a href="/docs" class="btn btn-secondary">📖 Swagger API</a>
        <button onclick="exportTasksCsv()" class="btn btn-secondary" id="export-btn">📥 Export Audit (CSV)</button>
        <button onclick="fetchTasks()" class="btn btn-secondary" id="refresh-btn">🔄 Refresh</button>
      </div>
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
        <div class="metric-label">Running in Cluster</div>
        <div class="metric-val" id="running-count" style="color: #38BDF8;">0</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Successfully Completed</div>
        <div class="metric-val" id="success-count" style="color: #34D399;">0</div>
      </div>
      <div class="metric-card">
        <div class="metric-label">Dead Letter Queue</div>
        <div class="metric-val" id="dlq-count" style="color: #F87171;">0</div>
      </div>
    </div>

    <!-- Quick Dispatcher -->
    <div class="dispatch-box">
      <h3>🚀 Dispatch Asynchronous Task to Cluster</h3>
      <form id="task-form" onsubmit="handleDispatch(event)">
        <div class="form-grid">
          <div class="form-group">
            <label>Task Title</label>
            <input type="text" id="task-title" class="form-control" placeholder="e.g. Generate Annual Ledger PDF" required />
          </div>
          <div class="form-group">
            <label>Task Type (Handler)</label>
            <select id="task-type" class="form-control">
              <option value="report_generation">report_generation (PDF Export)</option>
              <option value="data_processing">data_processing (ETL Batch)</option>
              <option value="email_dispatch">email_dispatch (Transactional Email)</option>
              <option value="system_cleanup">system_cleanup (Purge Temp Records)</option>
            </select>
          </div>
          <div class="form-group">
            <label>Priority (1 = Low, 10 = Critical)</label>
            <select id="task-priority" class="form-control">
              <option value="10">10 - Critical Priority 🔥</option>
              <option value="8" selected>8 - High Priority ⚡</option>
              <option value="5">5 - Normal Priority</option>
              <option value="1">1 - Low Priority</option>
            </select>
          </div>
          <div class="form-group">
            <label>Delay Countdown (Seconds, AWS SQS pattern)</label>
            <input type="number" id="task-delay" class="form-control" placeholder="0 (Immediate)" min="0" max="86400" value="0" />
          </div>
          <div class="form-group">
            <label>Webhook Callback URL (HMAC Signed)</label>
            <input type="url" id="task-webhook" class="form-control" placeholder="https://api.example.com/webhook (Optional)" />
          </div>
          <div class="form-group" style="align-self: flex-end;">
            <button type="submit" class="btn btn-success" style="width: 100%; padding: 10px; justify-content: center;" id="submit-btn">
              ⚡ Enqueue Task to RabbitMQ
            </button>
          </div>
        </div>
      </form>
    </div>

    <!-- Kanban Grid -->
    <div class="kanban-grid">
      <div class="kanban-col">
        <div class="col-header">
          <span>🟡 QUEUED / PENDING</span>
          <span id="badge-queued" class="tag">0</span>
        </div>
        <div id="col-queued"></div>
      </div>
      <div class="kanban-col">
        <div class="col-header">
          <span>🔵 RUNNING (Worker)</span>
          <span id="badge-running" class="tag">0</span>
        </div>
        <div id="col-running"></div>
      </div>
      <div class="kanban-col">
        <div class="col-header">
          <span>🟢 COMPLETED</span>
          <span id="badge-success" class="tag">0</span>
        </div>
        <div id="col-success"></div>
      </div>
      <div class="kanban-col">
        <div class="col-header">
          <div style="display: flex; align-items: center; gap: 6px;">
            <span>🔴 DEAD_LETTERED / FAILED</span>
            <span id="badge-dlq" class="tag">0</span>
          </div>
          <button onclick="replayAllDLQ()" class="btn btn-danger btn-sm" title="Replay all poisoned tasks back to queue" style="font-size: 0.7rem; padding: 3px 8px;">🔥 Replay All</button>
        </div>
        <div id="col-dlq"></div>
      </div>
    </div>
  </div>

  <script>
    let authToken = localStorage.getItem('cloudtask_token') || '';

    // Auto authenticate using demo seed user if no token exists
    async function ensureAuth() {
      if (authToken) return authToken;
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
        }
      } catch (err) {
        console.warn('Auto-login error:', err);
      }
      return authToken;
    }

    async function fetchTasks() {
      const token = await ensureAuth();
      if (!token) return;

      try {
        const res = await fetch('/api/v1/tasks?limit=50', {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (!res.ok) return;

        const data = await res.json();
        renderTasks(data.tasks || []);
      } catch (err) {
        console.error('Fetch error:', err);
      }
    }

    function renderTasks(tasks) {
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

        let progressHtml = '';
        if (status === 'RUNNING') {
          progressHtml = `
            <div class="progress-bar">
              <div class="progress-fill" style="width: ${Math.max(task.progress || 25, 20)}%;"></div>
            </div>
            <div style="font-size:0.75rem; color:#38BDF8;">Progress: ${task.progress || 25}%</div>
          `;
        }

        let actionHtml = '';
        if (status === 'QUEUED' || status === 'RUNNING') {
          actionHtml = `<div class="card-actions"><button onclick="cancelTask('${task.id}')" class="btn btn-danger btn-sm">Cancel Task</button></div>`;
        } else if (status === 'FAILED' || status === 'DEAD_LETTERED') {
          actionHtml = `<div class="card-actions"><button onclick="retryTask('${task.id}')" class="btn btn-success btn-sm">Retry Task</button></div>`;
        }

        let resultPreview = '';
        if (task.result) {
          resultPreview = `<pre style="font-size:0.7rem; color:#34D399; margin-top:6px; overflow:hidden; text-overflow:ellipsis;">${JSON.stringify(task.result)}</pre>`;
        }
        if (task.error_message) {
          resultPreview = `<div style="font-size:0.7rem; color:#F87171; margin-top:6px;">${task.error_message}</div>`;
        }

        let delayBadge = '';
        if (task.delay_seconds && task.delay_seconds > 0) {
          delayBadge = `<span class="tag" style="background:rgba(139,92,246,0.2); color:#A78BFA;">⏳ Delay ${task.delay_seconds}s</span>`;
        }
        let webhookBadge = '';
        if (task.webhook_url) {
          webhookBadge = `<span class="tag" style="background:rgba(16,185,129,0.2); color:#34D399;" title="${task.webhook_url}">🔗 Webhook</span>`;
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
          ${resultPreview}
          ${actionHtml}
        `;

        if (status === 'QUEUED' || status === 'PENDING') cols.QUEUED.appendChild(card);
        else if (status === 'RUNNING') cols.RUNNING.appendChild(card);
        else if (status === 'SUCCESS') cols.SUCCESS.appendChild(card);
        else cols.DEAD_LETTERED.appendChild(card);
      });

      document.getElementById('total-count').innerText = counts.total;
      document.getElementById('queued-count').innerText = counts.queued;
      document.getElementById('running-count').innerText = counts.running;
      document.getElementById('success-count').innerText = counts.success;
      document.getElementById('dlq-count').innerText = counts.dlq;

      document.getElementById('badge-queued').innerText = counts.queued;
      document.getElementById('badge-running').innerText = counts.running;
      document.getElementById('badge-success').innerText = counts.success;
      document.getElementById('badge-dlq').innerText = counts.dlq;
    }

    async function handleDispatch(e) {
      e.preventDefault();
      const token = await ensureAuth();
      const title = document.getElementById('task-title').value;
      const task_type = document.getElementById('task-type').value;
      const priority = parseInt(document.getElementById('task-priority').value, 10);
      const delay = parseInt(document.getElementById('task-delay').value, 10) || 0;
      const webhook = document.getElementById('task-webhook').value.trim() || null;
      const btn = document.getElementById('submit-btn');

      btn.innerText = '⏳ Dispatching...';
      try {
        const payloadBody = {
          title: title,
          task_type: task_type,
          priority: priority,
          delay_seconds: delay,
          payload: { timestamp: Date.now() }
        };
        if (webhook) payloadBody.webhook_url = webhook;

        const res = await fetch('/api/v1/tasks', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
          },
          body: JSON.stringify(payloadBody)
        });
        if (res.ok) {
          document.getElementById('task-title').value = '';
          document.getElementById('task-webhook').value = '';
          document.getElementById('task-delay').value = '0';
          btn.innerText = '✅ Dispatched!';
          setTimeout(() => btn.innerText = '⚡ Enqueue Task to RabbitMQ', 1500);
          fetchTasks();
        }
      } catch (err) {
        alert('Dispatch error: ' + err.message);
        btn.innerText = '⚡ Enqueue Task to RabbitMQ';
      }
    }

    async function replayAllDLQ() {
      if (!confirm('Replay all dead-lettered / failed tasks back into active queue?')) return;
      const token = await ensureAuth();
      try {
        const res = await fetch('/api/v1/tasks/dlq/replay-all', {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}` }
        });
        const data = await res.json();
        alert(data.message || 'DLQ tasks replayed');
        fetchTasks();
      } catch (err) {
        alert('DLQ Replay error: ' + err.message);
      }
    }

    async function exportTasksCsv() {
      const token = await ensureAuth();
      const btn = document.getElementById('export-btn');
      btn.innerText = '⏳ Exporting...';
      try {
        const res = await fetch('/api/v1/tasks/export?format=csv', {
          headers: { 'Authorization': `Bearer ${token}` }
        });
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
          alert('Export failed');
          btn.innerText = '📥 Export Audit (CSV)';
        }
      } catch (err) {
        alert('Export error: ' + err.message);
        btn.innerText = '📥 Export Audit (CSV)';
      }
      setTimeout(() => btn.innerText = '📥 Export Audit (CSV)', 2000);
    }

    async function cancelTask(taskId) {
      const token = await ensureAuth();
      await fetch(`/api/v1/tasks/${taskId}/cancel`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      });
      fetchTasks();
    }

    async function retryTask(taskId) {
      const token = await ensureAuth();
      await fetch(`/api/v1/tasks/${taskId}/retry`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      });
      fetchTasks();
    }

    // Initial fetch and 2-second live polling loop
    fetchTasks();
    setInterval(fetchTasks, 2500);
  </script>
</body>
</html>"""


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
