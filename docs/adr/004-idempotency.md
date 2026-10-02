# ADR 004: Two-Tier Idempotency Mechanism (Redis + PostgreSQL)

## Status
Accepted

## Context
Clients may submit duplicate task creation requests (e.g. on client network timeout), and workers may consume redelivered messages. We must prevent duplicate task creation and duplicate side-effect execution.

## Decision
We implement a **Two-Tier Idempotency Strategy**:
1. **Tier 1 (Fast path - Redis)**: When a client supplies an `idempotency_key`, the API Gateway / Task Service checks Redis with `SETNX` TTL. If found, it returns the cached task reference immediately.
2. **Tier 2 (Durable path - PostgreSQL)**: The `idempotency_key` column in the `tasks` table enforces a `UNIQUE` constraint in the database, preventing race conditions. Distributed workers acquire a Redis lock `lock:task:<id>` before running.

## Alternatives Considered
- **Database Unique Constraint Only**: Introduces heavy read/write lock contention on high-throughput ingest.
- **Redis Only**: Lacks durability if Redis restarts without AOF sync.

## Consequences
- **Positive**: Eliminates duplicate execution; sub-millisecond responses for duplicate submissions; durable system of record protection.
- **Negative**: Minimal storage overhead for storing idempotency keys for 24 hours.
