# CloudTask: Task State Machine & Lifecycle

CloudTask models task lifecycle deterministically with strict state transitions.

## Lifecycle States
1. **`PENDING`**: Task created and awaiting submission.
2. **`QUEUED`**: Task persisted to database and published into RabbitMQ.
3. **`RUNNING`**: Worker acquired the task, locked via Redis, and is actively executing.
4. **`SUCCESS`**: Execution succeeded, result recorded, notification published, message ACKed.
5. **`RETRY`**: Execution encountered transient failure. Exponential backoff applied before re-queueing.
6. **`DEAD_LETTERED`**: Task exceeded max retries (`current_attempt >= max_retries`). Routed to Dead Letter Queue (DLQ).
7. **`CANCELLED`**: User cancelled the task before completion.

## State Transition Diagram
```
           +-----------+
           |  PENDING  |
           +-----------+
                 |
                 v
           +-----------+     User Cancel
           |  QUEUED   |-------------------> [ CANCELLED ]
           +-----------+
                 |
                 v
           +-----------+
           |  RUNNING  |
           +-----------+
             /       \
            /         \
    Success/           \ Failure
          v             v
    +-----------+     +-----------+
    |  SUCCESS  |     |   RETRY   | (Attempts < MaxRetries)
    +-----------+     +-----------+
                            |
                     Backoff Timeout
                            v
                      [ QUEUED ]
                            |
                            | (Attempts >= MaxRetries)
                            v
                    +---------------+
                    | DEAD_LETTERED |
                    +---------------+
```
