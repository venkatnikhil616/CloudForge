# ADR 005: PostgreSQL as Authoritative System of Record

## Status
Accepted

## Context
A distributed task system requires persistent state tracking for users, task lifecycle status, execution attempts, historical logs, and schedules. We must maintain transactional integrity and relational consistency.

## Decision
We designate **PostgreSQL 16** as the authoritative System of Record (SoR). PostgreSQL is NOT used as a job queue; message queuing is strictly delegated to RabbitMQ.

## Alternatives Considered
- **MongoDB / NoSQL**: Flexible schemas, but lacks strict relational integrity and ACID guarantees across tasks and task execution attempt records.
- **Using PostgreSQL SKIP LOCKED as a Queue**: Creates database bloat and vacuum contention under heavy concurrent polling.

## Consequences
- **Positive**: Complete audit trail in `task_attempts`; ACID transactions during status transitions; robust schema migrations via Alembic / SQL scripts.
- **Negative**: Requires persistent volume storage and replication in Kubernetes StatefulSets.
