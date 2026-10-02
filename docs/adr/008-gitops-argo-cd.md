# ADR 008: GitOps Deployment Strategy with Argo CD

## Status
Accepted

## Context
To ensure continuous deployment is auditable, repeatable, and tamper-proof, application deployment definitions should be managed through version-controlled Git repositories rather than manual `kubectl` commands.

## Decision
We adopt **GitOps using Argo CD** and **Helm**:
1. The GitHub repository is the single source of truth for all Kubernetes deployment manifests.
2. Argo CD continuously reconciles the live Kubernetes cluster state with the Git repository target state.
3. Automated drift detection, self-healing, and declarative rollbacks.

## Alternatives Considered
- **Direct CI/CD Push (`kubectl apply` in GitHub Actions)**: Requires granting cluster administrative secrets to external CI runners.
- **Manual Helm Upgrades**: Error-prone, lack of deployment audit trails.

## Consequences
- **Positive**: Zero cluster credentials required in CI; instantaneous automated rollbacks by reverting Git commits; full auditability.
- **Negative**: Requires running the Argo CD operator inside the cluster.
