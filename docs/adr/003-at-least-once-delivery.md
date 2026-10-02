# ADR 003: At-Least-Once Delivery Model with Idempotent Processing

## Status
Accepted

## Context
In distributed task execution, network partitions, worker node crashes, or unacknowledged timeouts can prevent confirmation of task completion. Guaranteeing "strictly once" execution at the transport layer is impossible in distributed systems without unacceptable performance penalties.

## Decision
We adopt an **At-Least-Once Delivery** model paired with **Idempotent Task Processing**:
$$\text{At-Least-Once Delivery} + \text{Idempotent Processing} = \text{Reliable Execution}$$
Workers process messages and acknowledge them only after persisting execution results or failure states. If a worker crashes before ACK, RabbitMQ redelivers the message. The redelivered worker validates the task status and idempotency key before re-executing side effects.

## Alternatives Considered
- **At-Most-Once Delivery (auto-ack on receive)**: Can drop tasks if worker crashes midway through computation. Unacceptable for production workloads.
- **Claimed "Exactly-Once" Delivery**: Introduces distributed two-phase commit overhead and false sense of reliability.

## Consequences
- **Positive**: High resilience against network failures and sudden worker termination; zero task loss.
- **Negative**: Handlers and workers must implement idempotency checks.
