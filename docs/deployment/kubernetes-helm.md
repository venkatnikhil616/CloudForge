# Kubernetes & Helm Deployment Guide

## Direct Kubernetes Manifests
```bash
# 1. Create namespace
kubectl apply -f deployments/kubernetes/namespace.yaml

# 2. Deploy ConfigMaps, Secrets, and RBAC
kubectl apply -f deployments/kubernetes/configmap.yaml
kubectl apply -f deployments/kubernetes/secrets.yaml
kubectl apply -f deployments/kubernetes/rbac.yaml
kubectl apply -f deployments/kubernetes/network-policies.yaml

# 3. Deploy Stateful Infrastructure
kubectl apply -f deployments/kubernetes/postgres-statefulset.yaml
kubectl apply -f deployments/kubernetes/redis-statefulset.yaml
kubectl apply -f deployments/kubernetes/rabbitmq-statefulset.yaml

# 4. Deploy Stateless Services
kubectl apply -f deployments/kubernetes/auth-service.yaml
kubectl apply -f deployments/kubernetes/task-service.yaml
kubectl apply -f deployments/kubernetes/worker.yaml
kubectl apply -f deployments/kubernetes/scheduler.yaml
kubectl apply -f deployments/kubernetes/notification-service.yaml
kubectl apply -f deployments/kubernetes/api-gateway.yaml

# 5. Apply HPA & Ingress
kubectl apply -f deployments/kubernetes/hpa-worker.yaml
kubectl apply -f deployments/kubernetes/ingress.yaml
```

## Helm Deployment
```bash
helm upgrade --install cloudtask deployments/helm/cloudtask \
  --namespace cloudtask \
  --create-namespace \
  --values deployments/helm/cloudtask/values.yaml
```

## GitOps with Argo CD
```bash
kubectl apply -f deployments/helm/cloudtask/argo-cd/application.yaml
```
