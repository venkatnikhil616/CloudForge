# ADR 009: Directed Acyclic Graph (DAG) Task Dependency Resolution

## Status
Accepted

## Context
Complex business workflows require sequential task dependency execution (e.g., Task C must not execute until Task A and Task B have completed with SUCCESS).

## Decision
We implement **DAG Task Dependency Orchestration**:
- Tasks accept a `depends_on: List[str]` containing prerequisite task IDs.
- If prerequisites are not yet completed, the task enters `PENDING` state and is withheld from RabbitMQ.
- When any task succeeds, the worker triggers `check_and_trigger_dependents()`, inspecting pending tasks. When all prerequisites succeed, the task automatically transitions to `QUEUED` and is published to RabbitMQ.

## Consequences
- **Positive**: Enables multi-stage distributed data pipelines and workflows without external workflow engines.
- **Negative**: Requires checking dependent states upon task completion.
