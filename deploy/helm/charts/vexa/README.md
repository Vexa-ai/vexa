# vexa — v0.12 control-plane Helm chart

Deploys the full v0.12 stack to Kubernetes: the control plane **gateway · admin-api · meeting-api ·
runtime · agent-api**, the **terminal** web UI, and infra (`postgres` · `redis` · `minio` + a
`minio-init` bucket Job). The `runtime` spawns the bot and agent-worker as on-demand Pods
(`RUNTIME_BACKEND=k8s`, under the chart's ServiceAccount/RBAC); they are not long-running services.

```
            ┌──────────┐
  client ──>│ gateway  │──> admin-api ──┐
            └────┬─────┘                ├─> postgres
                 └────> meeting-api ────┘
                          │  └─> minio (recordings)
                          └─> runtime ──(kubectl run)──> bot Pod / agent-worker Pod
            agent-api ──> runtime                        redis (streams/pubsub)
```

## Install

```bash
helm upgrade --install vexa . -n vexa --create-namespace \
  --set global.imageTag=YYMMDD-HHMM \
  --set secrets.adminApiToken=$ADMIN_TOKEN \
  --set secrets.internalApiSecret=$INTERNAL_API_SECRET
```

See [`../../README.md`](../../README.md) for the cookbook (local k3s smoke, managed backing,
ingress) and the values table. Key knobs: `global.imageTag`, `runtime.backend`
(`k8s`|`docker`|`process`), `secrets.*` (or `secrets.existingSecretName`), `postgres/redis/minio.enabled`,
`pgbouncer.enabled`, `ingress.*`.

## Validate (no cluster)

```bash
helm lint .
helm template vexa . -n vexa -f values-test.yaml
```
