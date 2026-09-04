# ADR 007: Kubernetes Orchestration with Declarative Workloads

## Status
Accepted

## Context
CloudTask requires automated horizontal scaling, self-healing container processes, service discovery, rolling deployments, and resource guarantees across production environments.

## Decision
We deploy CloudTask on **Kubernetes (K8s)**:
- **Stateless Services** (`api-gateway`, `auth-service`, `task-service`, `worker`, `scheduler`, `notification-service`) deployed as `Deployments` with RollingUpdate strategies.
- **Stateful Infrastructure** (`postgres`, `redis`, `rabbitmq`) deployed as `StatefulSets` with `PersistentVolumeClaims`.
- **Worker Autoscaling** controlled via `HorizontalPodAutoscaler (HPA)`.
- **Security Isolation** enforced with `NetworkPolicies` and non-root service accounts.

## Alternatives Considered
- **Docker Compose in Production**: Lacks dynamic self-healing, multi-node scheduling, and automated horizontal autoscaling.
- **Serverless (AWS Lambda / Cloud Functions)**: Limits persistent worker concurrency and custom AMQP queue listeners.

## Consequences
- **Positive**: Production-grade cloud-native scalability; portable across GCP (GKE), AWS (EKS), Azure (AKS), and local Minikube/Kind.
- **Negative**: Requires Kubernetes operational familiarity and manifest maintenance.
