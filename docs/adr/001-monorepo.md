# ADR 001: Monorepo Architecture for CloudTask Services

## Status
Accepted

## Context
CloudTask consists of multiple decoupled microservices (`api-gateway`, `auth-service`, `task-service`, `worker`, `scheduler`, `notification-service`) and a shared library (`pkg/`). We needed to decide whether to place them in multiple separate Git repositories (polyrepo) or a single unified Git repository (monorepo).

## Decision
We chose a **Monorepo** structure. All microservices, shared domain packages, Kubernetes manifests, Helm charts, CI/CD pipelines, and documentation reside in a single repository.

## Alternatives Considered
- **Polyrepo (Multiple Repositories)**: Distributing each microservice and `pkg/` into isolated repositories.
  - *Disadvantages*: High overhead for cross-cutting schema changes, difficult version synchronization, duplicated CI/CD boilerplate.

## Consequences
- **Positive**: Atomic commits across services and shared packages; simplified local Docker Compose development; unified CI/CD pipelines and single source of truth for architectural documentation.
- **Negative**: Repo size can increase over time; requires structured folder-based CI trigger scoping.
