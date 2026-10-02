# ADR 002: RabbitMQ as Primary Asynchronous Message Broker

## Status
Accepted

## Context
CloudTask requires a robust, high-performance messaging backbone to decouple task producers from task consumers, support task prioritization (1 to 10), acknowledgements, and dead-letter queue routing.

## Decision
We selected **RabbitMQ (AMQP 0-9-1)** with topic exchanges (`cloudtask.events`), durable priority queues (`cloudtask.tasks`), and dead-letter exchanges (`cloudtask.dlx`).

## Alternatives Considered
- **Redis Streams / PubSub**: Lightweight, but lacks native advanced routing, durable queue-level priority sorting, and enterprise dead-lettering semantics.
- **Apache Kafka**: Exceptional throughput for high-volume event streams, but higher operational overhead for job-queue-style single-message acknowledgements and priority handling.
- **AWS SQS**: Managed solution, but introduces cloud provider lock-in and limits local containerized developer workflows.

## Consequences
- **Positive**: Native message persistence, explicit manual consumer ACK/NACK, native Dead Letter Exchanges, and built-in priority queue support.
- **Negative**: Requires managing RabbitMQ broker cluster state and monitoring queue memory limits.
