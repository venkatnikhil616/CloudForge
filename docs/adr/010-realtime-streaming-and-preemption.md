# ADR 010: Real-Time SSE Streaming and Redis Pub/Sub Worker Preemption

## Status
Accepted

## Context
Users require live progress visibility without repetitive polling, and the ability to immediately abort running tasks to release cluster resources.

## Decision
1. **Server-Sent Events (SSE)**: Added `/api/v1/tasks/{id}/stream` backed by Redis pub/sub (`task:progress:{id}`) emitting real-time percentage progress.
2. **Worker Preemption**: User cancellation (`POST /tasks/{id}/cancel`) publishes an abort signal to Redis channel `task:abort:{id}`. The executing worker actively monitors this channel and cancels the task coroutine immediately.

## Consequences
- **Positive**: Instant UI progress updates and instantaneous worker interruption; zero wasted CPU/RAM on aborted jobs.
- **Negative**: Requires workers to maintain an async abort listener alongside the task execution coroutine.
