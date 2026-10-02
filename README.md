# CloudTask ⚡
### Production-Grade Distributed Task Processing Platform

[![CI Pipeline](https://github.com/venkatnikhil616/CloudForge/actions/workflows/ci.yml/badge.svg)](https://github.com/venkatnikhil616/CloudForge/actions)
[![Live Demo](https://img.shields.io/badge/Render-Live%20Platform-success?logo=render&logoColor=white)](https://cloudtask-platform.onrender.com/dashboard)
[![Swagger Docs](https://img.shields.io/badge/OpenAPI-Swagger%20Docs-blue?logo=swagger)](https://cloudtask-platform.onrender.com/docs)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python Version](https://img.shields.io/badge/Python-3.11%2B-brightgreen.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg)](https://fastapi.tiangolo.com)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-Cloud--Native-326CE5.svg)](https://kubernetes.io/)
[![ArgoCD](https://img.shields.io/badge/GitOps-Argo%20CD-EF7B42.svg)](https://argoproj.github.io/cd/)

CloudTask is a high-throughput, distributed asynchronous task orchestration and execution platform designed for modern microservice architectures. Built with **Python (FastAPI)**, **PostgreSQL**, **Redis**, and **RabbitMQ**, CloudTask guarantees durable at-least-once message processing, distributed two-tier idempotency, priority scheduling, automated exponential backoff retries, Dead-Letter Queue (DLQ) redrive, and comprehensive observability.

---

## 🌐 Live Cloud Deployment

CloudTask is deployed live on Render with automated database migrations and zero-downtime health probes:

| Service / Interface | Public URL | Description |
| :--- | :--- | :--- |
| **🚀 Operations Dashboard** | [cloudtask-platform.onrender.com/dashboard](https://cloudtask-platform.onrender.com/dashboard) | Real-time monitoring, task modals, execution modes, DLQ redrive & CSV audit export |
| **📖 Interactive API Docs** | [cloudtask-platform.onrender.com/docs](https://cloudtask-platform.onrender.com/docs) | Interactive Swagger OpenAPI documentation & test console |
| **❤️ Health Probe (Liveness)** | [cloudtask-platform.onrender.com/health/live](https://cloudtask-platform.onrender.com/health/live) | Gateway & downstream microservice health status |
| **🔍 Health Probe (Readiness)** | [cloudtask-platform.onrender.com/health/ready](https://cloudtask-platform.onrender.com/health/ready) | Deep DB & Redis connectivity verification |

### Default Admin Credentials
For testing role-protected endpoints or dashboard administrative actions:
* **Email:** `admin@cloudtask.dev`
* **Password:** `AdminSecurePass123!`

---

## 🖥️ Web Operations Dashboard Walkthrough

The built-in Web Operations Dashboard (`/dashboard`) provides end-to-end task lifecycle management, priority execution orchestration, and real-time operational oversight:

1. **Live Metric Cards**:
   - **Pending & Processing**: Instant visibility into queue depth and active worker utilization.
   - **Completed & DLQ**: Real-time counters of successfully completed jobs and failed tasks routed to the Dead-Letter Queue.
   - **Performance Stats**: Continuous calculation of average task execution latency and platform success rate percentages.

2. **Dedicated New Task Modal Dialog**:
   - Centered modal dialog (Linear/Jira-inspired) accessible directly via the top navigation bar **+ New Task** button.
   - Dynamic contextual auto-naming suggestions, handler type selector (`email_dispatch`, `data_sync`, `report_generation`, `image_processing`), priority slider (1 Low to 10 Critical), execution delay configuration, JSON payload editor, and duplicate guard toggle.
   - In-modal return execution streaming with real-time status feedback upon submission.

3. **Execution Mode Switcher (`Auto-Start` vs `Manual Staged`)**:
   - Top-level operational mode selector allowing instant toggling between execution strategies:
     - **Auto-Start**: Tasks are immediately dispatched to RabbitMQ worker priority queues upon enqueueing.
     - **Manual (Staged)**: Tasks accumulate safely in a staged queue (`QUEUED`), allowing operators to inspect batches and trigger deterministic, priority-ordered execution on demand.

4. **1-Click Priority Demo Seeding**:
   - When the active queue is empty, the dashboard provides a 1-click **Seed Demo Tasks** action that enqueues three multi-priority tasks (P10 Critical, P8 High, P5 Normal) to demonstrate priority scheduling order in real time.

5. **Manual Batch Staging & Priority Execution ("Start Process")**:
   - Operators can stage tasks and initiate sequential execution strictly ordered by priority (P10 down to P1) with paced worker processing to visualize scheduling mechanics.

6. **Real-Time Pre-Flight Duplicate Detection & Duplicate Guard**:
   - Dynamic debounced collision checking as task titles are typed in the dispatcher or modal.
   - **Duplicate Detection Guard Toggle**: Automatically rejects duplicate active tasks (`QUEUED`, `PENDING`, `RUNNING`) with HTTP 409 Conflict when enabled; toggleable to allow intentional duplicates.
   - **Visual Duplicate Tags**: Tasks with matching titles across the fleet display an amber `Duplicate (<count>)` badge on Kanban cards.
   - **Duplicates-Only Filter**: Toolbar toggle to isolate colliding task submissions across states.

7. **Interactive Task Inspection Modal**:
   - Comprehensive task inspection dialog (Flower/Temporal-inspired) opening upon clicking any task card.
   - Displays complete JSON payload, attempt history, retry backoff intervals, worker node allocation, execution timing breakdowns, and detailed error tracebacks.

8. **Enterprise Toast Notification & Action Center**:
   - Non-blocking, real-time toast feedback for all operational events (task submissions, batch triggers, queue purging, DLQ redrives, export status).

9. **CSV Audit Export & Modal Preview**:
   - Click **Export CSV** to open the audit export dialog with instant modal preview and direct file download.
   - Features 1-click clipboard copy of raw CSV records and dual fallback (backend streaming + client-side memory dump).

10. **Dead-Letter Queue (DLQ) & 1-Click Replay**:
    - Inspect poisoned or failed tasks with detailed failure reasons and retry attempt counters.
    - Click **Replay All** to redrive failed tasks back into worker priority queues with exponential backoff reset.

11. **Clear History Action**:
    - One-click **Clear History** button purges completed and failed tasks from the dashboard to maintain optimal UI responsiveness.

---

## 🏛️ System Architecture

```
                                      +-------------------------+
                                      |   Client Application    |
                                      +-------------------------+
                                                   |
                                                   v
                                      +-------------------------+
                                      |      NGINX Ingress      |
                                      +-------------------------+
                                                   |
                                                   v
                                      +-------------------------+
                                      |   API Gateway (:8000)   |
                                      | - Rate Limiter (Redis)  |
                                      | - Correlation Tracing   |
                                      | - Security Proxy        |
                                      +-------------------------+
                                             /            \
                                            /              \
                                           v                v
                        +----------------------+    +----------------------+
                        | Auth Service (:8001) |    | Task Service (:8002) |
                        | - JWT Auth & Hashing |    | - Task Lifecycle     |
                        | - User Management    |    | - Priority Queuing   |
                        +----------------------+    +----------------------+
                                   \                    /           |
                                    \                  /            |
                                     v                v             v
                              +--------------------------+   +-------------------+
                              | PostgreSQL 16 (Authoritative)|   | RabbitMQ Broker   |
                              | - System of Record       |   | - Topic Exchange  |
                              | - Users, Tasks, Attempts |   | - Priority Queues |
                              +--------------------------+   | - Dead Letter DLX |
                                            ^                +-------------------+
                                            |                  /        \
                                     +---------------+        /          \
                                     |  Redis Cache  |       v            v
                                     | - Mutex Lock  | +-----------+ +-------------+
                                     | - Rate Limits | | Workers   | | Notification|
                                     +---------------+ | (Replicas)| | Service     |
                                                       +-----------+ +-------------+
```

---

## ✨ Key Features

- 🔒 **Stateless JWT Authentication**: Secure password hashing with bcrypt, JWT token issuing, verification, and role-based access control.
- 🛡️ **Active Duplicate Task Detection**: Active collision detection rejecting duplicate active submissions (`QUEUED`, `PENDING`, `RUNNING`) with `HTTP 409 Conflict`, pre-flight checking (`POST /api/v1/tasks/check-duplicate`), and cluster scanner (`GET /api/v1/tasks/duplicates`).
- ⚡ **Priority-Based Task Queuing & Batch Staging**: 10 levels of task priority (1 Low to 10 Critical) dispatched via RabbitMQ, with manual batch staging and sequential priority execution ("Start Process").
- 🎛️ **Dual Execution Modes**: Dynamic switching between automatic immediate worker dispatch (`auto`) and operator-controlled batch staging (`manual`).
- 🔍 **Interactive Task Inspector & Creation Modals**: Deep inspection modals (Flower/Temporal style) displaying payloads, retry counts, latency breakdowns, and worker assignments, alongside Jira/Linear-style task creation modals.
- 🛡️ **At-Least-Once Delivery & Idempotency**: Redis distributed mutex locking (`Redlock` pattern) combined with PostgreSQL unique constraints prevent duplicate execution.
- 🔁 **Controlled Exponential Backoff & DLQ**: Configurable retry policies ($5 \times 3^{\text{attempt}}$ seconds) with automatic escalation to Dead Letter Queue (`cloudtask.tasks.dlq`).
- ⏱️ **Distributed Scheduler**: Cron-based and interval job execution with Redis leader election to prevent duplicate task emission across replicas.
- 📬 **Event-Driven Notifications & Toast Alerts**: Asynchronous event dispatching for task completions, failures, and real-time dashboard action toasts.
- 📋 **Audit Logging & CSV Export**: Comprehensive operational audit export with direct CSV download and modal preview with 1-click clipboard copy.
- 🧹 **State Cleanup & Clear History**: Instant one-click purging of finished and dead-lettered tasks to maintain high UI responsiveness.
- ☸️ **Cloud-Native Kubernetes & Helm**: Full declarative manifests, StatefulSets with PVCs, HPA autoscaling, NetworkPolicies, and Helm charts.
- 🚀 **GitOps Deployment**: Declarative sync and automated deployments via Argo CD.
- 📊 **End-to-End Observability**: Custom Prometheus metrics exporter, structured JSON logging with correlation IDs for Loki, and pre-configured Grafana dashboards.

---

## 💻 Quick API Usage (cURL Examples)

All endpoints can be exercised against the live platform or your local instance.

### 1. Health & Liveness
```bash
curl -s https://cloudtask-platform.onrender.com/health/live | jq .
```

### 2. Authenticate & Obtain Access Token
```bash
TOKEN=$(curl -s -X POST https://cloudtask-platform.onrender.com/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@cloudtask.dev", "password": "AdminSecurePass123!"}' | jq -r .access_token)

echo "JWT: $TOKEN"
```

### 3. Enqueue a Background Task
```bash
curl -s -X POST https://cloudtask-platform.onrender.com/api/v1/tasks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "title": "Batch Data Ingestion",
    "task_type": "data_sync",
    "priority": 8,
    "prevent_duplicates": true,
    "payload": {"records": 5000, "source": "s3://datasets/daily.parquet"}
  }' | jq .
```

### 4. Pre-Flight Duplicate Verification
```bash
curl -s -X POST https://cloudtask-platform.onrender.com/api/v1/tasks/check-duplicate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"title": "Batch Data Ingestion", "task_type": "data_sync"}' | jq .
```

### 5. Configure Execution Mode (Auto-Start vs Manual Staging)
```bash
# Switch execution mode to manual staged batching
curl -s -X POST https://cloudtask-platform.onrender.com/api/v1/tasks/execution-mode \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"mode": "manual"}' | jq .

# Query current execution mode and staged backlog count
curl -s -X GET https://cloudtask-platform.onrender.com/api/v1/tasks/execution-mode \
  -H "Authorization: Bearer $TOKEN" | jq .
```

### 6. Trigger Staged Priority Execution
```bash
curl -s -X POST https://cloudtask-platform.onrender.com/api/v1/tasks/start-processing \
  -H "Authorization: Bearer $TOKEN" | jq .
```

### 7. Query Task Status
```bash
curl -s -X GET "https://cloudtask-platform.onrender.com/api/v1/tasks/<TASK_ID>" \
  -H "Authorization: Bearer $TOKEN" | jq .
```

### 8. Clear Finished History
```bash
curl -s -X POST https://cloudtask-platform.onrender.com/api/v1/tasks/clear-history \
  -H "Authorization: Bearer $TOKEN" | jq .
```

### 9. Export Task Audit History to CSV
```bash
curl -s -X GET "https://cloudtask-platform.onrender.com/api/v1/tasks/export?format=csv" \
  -H "Authorization: Bearer $TOKEN" -o tasks_audit_log.csv

head -n 5 tasks_audit_log.csv
```

### 10. Replay Dead Letter Queue (DLQ)
```bash
curl -s -X POST https://cloudtask-platform.onrender.com/api/v1/tasks/dlq/replay-all \
  -H "Authorization: Bearer $TOKEN" | jq .
```

---

## 📂 Monorepo Repository Layout

```
.
├── services/
│   ├── api-gateway/            # Unified API gateway, dashboard UI & rate limiting
│   ├── auth-service/           # User authentication, registration & JWT issuance
│   ├── task-service/           # Task CRUD, state tracker & RabbitMQ publisher
│   ├── worker/                 # Distributed async task consumer & executor
│   ├── scheduler/              # Cron & delayed job detector with leader lock
│   └── notification-service/   # Asynchronous notification consumer
├── pkg/                        # Reusable shared core modules
│   ├── config.py               # Centralized pydantic-settings configuration
│   ├── database.py             # Async SQLAlchemy 2.0 engine & session factory
│   ├── redis_client.py         # Redis connection, distributed locks, rate limits
│   ├── messaging.py            # RabbitMQ publisher & consumer with DLX
│   ├── security.py             # Bcrypt hashing & JWT token management
│   ├── logger.py               # Structured JSON logger with Correlation IDs
│   └── models/                 # SQLAlchemy models (User, Task, Attempt, Schedule)
├── database/
│   ├── migrations/             # PostgreSQL database schemas
│   └── seeds/                  # Initial seed data generator
├── deployments/
│   ├── kubernetes/             # Production K8s manifests (StatefulSets, HPA, NetworkPolicies)
│   └── helm/cloudtask/         # Production Helm chart & Argo CD application
├── monitoring/
│   ├── prometheus/             # Prometheus scrape configs & alert rules
│   ├── grafana/                # Provisioned Grafana datasources & dashboards
│   └── loki/                   # Structured logging aggregation configs
├── sdk/                        # Official CloudTask Python Client SDK
│   └── cloudtask/              # Async/sync client with retry & signature verification
├── examples/                   # Practical integration examples & workflow scripts
├── docs/
│   ├── adr/                    # Architecture Decision Records (ADRs)
│   ├── architecture/           # System architecture diagrams & state machine
│   ├── api/                    # OpenAPI endpoint specifications
│   ├── deployment/             # Deployment runbooks for Docker and K8s
│   └── operations/             # Production operational runbooks
├── tests/
│   ├── unit/                   # Unit tests (models, security, retry algorithms)
│   ├── integration/            # Service integration tests
│   └── e2e/                    # Full end-to-end task execution workflow tests
├── docker-compose.yml          # Complete 1-command local development stack
├── Makefile                    # Developer automation tasks
└── README.md
```

---

## 🚀 Quick Start (Local Development)

### 1. Start Full Infrastructure & Services with Docker Compose
```bash
# Clone the repository
git clone https://github.com/venkatnikhil616/CloudForge.git
cd CloudForge

# Launch entire platform
make docker-up
```

### 2. Available Local Service Endpoints
| Component | URL | Credentials / Notes |
| :--- | :--- | :--- |
| **API Gateway & Dashboard** | `http://localhost:8000` / `/dashboard` | Unified Entry Point |
| **Auth Service** | `http://localhost:8001` | Direct Service Port |
| **Task Service** | `http://localhost:8002` | Direct Service Port |
| **RabbitMQ UI** | `http://localhost:15672` | `guest` / `guest` |
| **Prometheus** | `http://localhost:9090` | Metrics Scraper |
| **Grafana** | `http://localhost:3000` | `admin` / `admin` |
| **Loki** | `http://localhost:3100` | Log Aggregator |

---

## 🧪 Testing

Run the automated test suite locally:
```bash
# Install dependencies
pip install -r requirements.txt

# Run all unit and integration tests
make test
```

---

## 📖 Architectural Decision Records (ADRs)

Key architectural choices are formally documented under [`docs/adr/`](docs/adr/):
- [ADR 001: Monorepo Architecture](docs/adr/001-monorepo.md)
- [ADR 002: RabbitMQ as Message Broker](docs/adr/002-rabbitmq.md)
- [ADR 003: At-Least-Once Delivery & Idempotency](docs/adr/003-at-least-once-delivery.md)
- [ADR 004: Two-Tier Idempotency Mechanism](docs/adr/004-idempotency.md)
- [ADR 005: PostgreSQL as System of Record](docs/adr/005-postgresql-source-of-truth.md)
- [ADR 006: Ephemeral Redis Caching & Locking](docs/adr/006-redis-usage.md)
- [ADR 007: Kubernetes Orchestration](docs/adr/007-kubernetes.md)
- [ADR 008: GitOps Deployment with Argo CD](docs/adr/008-gitops-argo-cd.md)
- [ADR 009: DAG Workflow Orchestration](docs/adr/009-dag-workflow-orchestration.md)
- [ADR 010: Realtime Streaming & Task Preemption](docs/adr/010-realtime-streaming-and-preemption.md)
- [ADR 011: Enterprise Task Patterns](docs/adr/011-enterprise-task-patterns.md)

---

## 📜 License
This project is open source and available under the [MIT License](LICENSE).
