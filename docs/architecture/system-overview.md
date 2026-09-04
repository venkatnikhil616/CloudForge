# CloudTask: System Architecture Overview

CloudTask is a cloud-native, distributed asynchronous task processing platform designed for reliability, horizontal scalability, and observability.

## Architecture Diagram

```
                     +---------------------------------------+
                     |                Client                 |
                     +---------------------------------------+
                                         |
                                         v
                     +---------------------------------------+
                     |             NGINX Ingress             |
                     +---------------------------------------+
                                         |
                                         v
                     +---------------------------------------+
                     |          API Gateway (:8000)          |
                     | - Rate Limiting (Redis)               |
                     | - Correlation IDs (X-Correlation-ID)  |
                     | - Reverse Proxy & Versioning          |
                     +---------------------------------------+
                               /                   \
                              /                     \
                             v                       v
    +------------------------------+     +-------------------------------+
    |     Auth Service (:8001)     |     |     Task Service (:8002)      |
    | - Registration & Login       |     | - Task CRUD & State Tracker   |
    | - JWT Verification           |     | - Idempotency Enforcement     |
    +------------------------------+     +-------------------------------+
                   \                                     /
                    \                                   /
                     v                                 v
          +-----------------------+        +-----------------------+
          |  PostgreSQL 16 (DB)   |<-------|      Redis Cache      |
          | - System of Record    |        | - Mutex Distributed   |
          | - Users, Tasks, Logs  |        | - Token Bucket Limits |
          +-----------------------+        +-----------------------+
                                                       ^
                                                       |
                     +---------------------------------+
                     |
                     v
       +----------------------------+
       |   RabbitMQ Message Broker  |
       | - Topic: cloudtask.events  |
       | - Queue: cloudtask.tasks   |
       | - DLX: cloudtask.dlx       |
       +----------------------------+
             |                 \
             v                  v
+-----------------------+   +-------------------------------+
|  Distributed Workers  |   |  Notification Service (:8093) |
| - Idempotent Runner   |   | - Consumes task events        |
| - Exponential Backoff |   | - Dispatches simulated alerts |
| - Dead Letter Routing |   +-------------------------------+
+-----------------------+
```

## Service Communication
- **Synchronous**: API Gateway uses HTTP/REST to communicate with Auth and Task services with correlation ID tracing.
- **Asynchronous**: Task dispatching, retries, and notifications communicate via RabbitMQ AMQP topics.
