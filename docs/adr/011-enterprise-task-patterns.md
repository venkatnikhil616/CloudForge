# ADR 011: Enterprise Task Patterns: Webhooks, Batching, DLQ Redrive, and Audit Export

## Status
Accepted

## Context
High-throughput distributed production applications (modeled after AWS SQS, Celery, Temporal, and Stripe) require real-world capabilities beyond basic point-to-point task queues:
1. **Outgoing Webhook Notifications**: External services cannot hold HTTP connections open indefinitely and should not continuously poll; they expect authenticated, tamper-proof callback events.
2. **High-Throughput Batch Ingestion**: Ingesting high volumes of tasks one-by-one introduces excessive HTTP connection overhead.
3. **Dead-Letter Queue (DLQ) Redrive**: When downstream dependencies fail (e.g. database maintenance or 3rd-party API outages), failed messages accumulate in the DLQ and require one-click bulk replay once resolved.
4. **Scheduled Delays**: Certain jobs require execution only after a specific countdown (e.g., billing grace periods, verification timeouts).
5. **Audit & Compliance Export**: SOC2, ISO27001, and regulatory frameworks require auditable CSV / JSON data exports of task executions, error logs, and attempt histories.

## Decision
1. **HMAC-SHA256 Webhooks**: The notification service delivers task completion and failure events to user-specified `webhook_url` endpoints signed with HMAC-SHA256 headers (`X-CloudTask-Signature: t={timestamp},v1={hash}`).
2. **Batch Task Ingestion (`POST /api/v1/tasks/batch`)**: Accepts up to 100 tasks per request with atomic persistence and multi-message RabbitMQ publishing.
3. **Dead-Letter Queue Redrive (`POST /api/v1/tasks/dlq/replay-all`)**: Replays all dead-lettered and failed tasks back into the active cluster queue.
4. **Delayed Task Execution (`delay_seconds`)**: Tasks with delays are held in `PENDING` state with a target `scheduled_at` timestamp and automatically unlocked by the scheduler once elapsed.
5. **Audit Export (`GET /api/v1/tasks/export?format=csv`)**: Exports task audit trails in RFC 4180 CSV or JSON format with correlation IDs, attempt metrics, and timestamps.

## Consequences
- **Positive**: Enterprise-ready interoperability matching AWS SQS, Temporal, and Stripe API standards.
- **Negative**: Webhook delivery requires network egress handling, retries, and cryptographic verification on the consumer's side.
