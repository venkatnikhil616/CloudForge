# Local Development & Deployment Guide

## Prerequisites
- Docker & Docker Compose
- Python 3.11+
- Make

## Quick Start (One Command)
```bash
make docker-up
```

This starts:
- PostgreSQL on port `5432`
- Redis on port `6379`
- RabbitMQ & Management UI on ports `5672` & `15672`
- Auth Service on port `8001`
- Task Service on port `8002`
- Worker (2 replicas)
- Scheduler
- Notification Service
- API Gateway on port `8000`
- Prometheus on port `9090`
- Grafana on port `3000` (admin/admin)
- Loki on port `3100`

## Stopping the Platform
```bash
make docker-down
```
