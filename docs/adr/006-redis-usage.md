# ADR 006: Redis for Ephemeral Caching, Locks, and Rate Limiting

## Status
Accepted

## Context
Certain distributed operations require low-latency operations that should not burden PostgreSQL: distributed mutexes, token bucket rate limiting, and short-term idempotency caching.

## Decision
We utilize **Redis** strictly for high-speed ephemeral data:
1. Distributed Locking (`lock:task:<id>`, `lock:scheduler:leader`).
2. API Rate Limiting per IP / API client.
3. Fast-path Idempotency Key cache.
Redis is **never** treated as the primary source of truth for durable business records.

## Alternatives Considered
- **ZooKeeper / etcd for Locking**: Heavyweight to operate alongside PostgreSQL and RabbitMQ.
- **In-memory Service Locks**: Ineffective across multi-replica Kubernetes Deployments.

## Consequences
- **Positive**: Sub-millisecond distributed coordination and rate limiting; protects PostgreSQL from high-frequency polling.
- **Negative**: Cache miss / cold restart must gracefully fallback to PostgreSQL.
