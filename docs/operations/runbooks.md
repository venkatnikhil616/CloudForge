# Operational Runbooks

## 1. Handling Dead Letter Queue (DLQ) Spikes
1. Navigate to Grafana Dashboard (`http://localhost:3000/d/cloudtask-overview`).
2. Inspect Prometheus metric `worker_tasks_failed_total`.
3. Check worker logs in Loki for stack traces matching the DLQ tasks:
   ```logql
   {service="worker"} |= "DEAD_LETTERED"
   ```
4. Resolve underlying issue (e.g., third-party endpoint downtime).
5. Replay DLQ messages via `POST /api/v1/tasks/{task_id}/retry`.

## 2. Scaling Worker Replicas
To manually scale worker nodes:
```bash
kubectl scale deployment worker --replicas=10 -n cloudtask
```
Worker Horizontal Pod Autoscaler (HPA) automatically adjusts replicas based on CPU/memory load.
