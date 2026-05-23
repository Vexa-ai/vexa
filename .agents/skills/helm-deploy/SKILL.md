---
name: helm-deploy
description: Deploy and verify Vexa on Kubernetes with the upstream deploy/helm charts. Use for Helm/LKE release validation, stage throwaway Helm lanes, chart rendering, install, upgrade, health checks, service URL checks, and cleanup.
---

# Helm Deploy

## Start Here

Read the official upstream Helm docs before running or changing anything:

1. `deploy/helm/README.md`
2. `deploy/helm/charts/vexa/README.md`
3. `deploy/helm/charts/vexa/values.yaml`

Use `deploy/helm/charts/vexa-lite` only when the task explicitly asks for the
Lite Helm chart. The normal full-stack release lane uses `deploy/helm/charts/vexa`.

## Rules

- Follow the upstream Helm chart and values model.
- Use Helm v3 and a real kubeconfig supplied by the environment or by
  `throwaway-infra-deploy`.
- Pin images to an explicit immutable tag for release validation.
- Do not use Docker Compose or Vexa Lite commands as substitutes for Helm.
- Do not print kubeconfig contents, token values, generated API keys, database
  passwords, transcription tokens, or private values files.
- If official docs are missing or contradictory, stop and report the exact gap.

## Deploy Flow

1. Confirm the target checkout is the upstream Vexa repo.
2. Confirm `kubectl` can reach the intended cluster and namespace.
3. Render first:

```bash
helm template vexa ./deploy/helm/charts/vexa --values <values-file>
```

4. Lint or run chart tests when available:

```bash
bash deploy/helm/tests/test_template.sh
bash deploy/helm/tests/test_helm_lint.sh
```

5. Install or upgrade:

```bash
helm upgrade --install vexa ./deploy/helm/charts/vexa \
  --namespace <namespace> \
  --create-namespace \
  --values <values-file>
```

6. Wait for deployments and inspect pods, services, endpoints, and events:

```bash
kubectl -n <namespace> rollout status deploy --timeout=10m
kubectl -n <namespace> get pods,svc,endpoints
```

7. Verify dashboard and gateway URLs from outside the cluster. For throwaway
   NodePort lanes, use the public dashboard/API URLs recorded by
   `throwaway-infra-deploy`.

## Completion Criteria

Treat the Helm deployment as incomplete until:

- `helm template` succeeds;
- install/upgrade succeeds;
- all in-scope deployments roll out;
- pods are Running/Ready with no CrashLoopBackOff;
- services have endpoints;
- dashboard and gateway are reachable from the operator machine;
- bot runtime mode and bot image settings match the intended release tag;
- release-specific checks from `state.md` pass or are recorded as blocked.

## Cleanup

For disposable Helm lanes, prefer `throwaway-infra-deploy` teardown so only the
recorded resources are destroyed. If asked to remove only the Helm release:

```bash
helm uninstall vexa --namespace <namespace>
```

Report namespace, chart path, image tag, public URLs, checks run, and final
pass/fail state. Redact secrets.
