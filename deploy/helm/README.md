# deploy/helm — the v0.12 control-plane chart (Kubernetes)

The `helm` target of the lite/compose/helm trio: the full v0.12 stack as a Kubernetes release —
the control plane **gateway · admin-api · meeting-api · runtime · agent-api**, the **terminal** web
UI, and infra (`postgres:17` · `redis:7` · `minio` + a `minio-init` bucket Job). The **terminal** is
the human front door (Next.js; proxies `/ws` → gateway and REST/login → agent-api/admin-api
server-side); the gateway stays the API front door for programmatic use. The difference from compose
is the **spawn substrate**: on k8s the `runtime` launches the bot and agent-worker as **Pods** (via
`kubectl`, under a chart-provided ServiceAccount/RBAC), selected by `RUNTIME_BACKEND=k8s` — not the
host Docker socket.

## Chart

[`charts/vexa`](charts/vexa/) — the full multi-service deployment. Production-hardened scaffolding
carried from the 0.10.6.3 baseline: zero-downtime `RollingUpdate` (maxSurge 1 / maxUnavailable 0),
PodDisruptionBudgets on stateless services, the Redis durability paired invariant, secret-sourced
DB/admin/provider credentials, optional PgBouncer for managed Postgres.

## Quick start (any cluster)

```bash
# 1. Pin the image tag your build produced (build-once promotion), fill secrets.
helm upgrade --install vexa deploy/helm/charts/vexa -n vexa --create-namespace \
  --set global.imageTag=YYMMDD-HHMM \
  --set secrets.adminApiToken=$ADMIN_TOKEN \
  --set secrets.internalApiSecret=$INTERNAL_API_SECRET \
  --set secrets.transcriptionServiceToken=$STT_TOKEN \
  --wait --timeout 10m

# 2. Watch it come up, then probe the front door.
kubectl -n vexa rollout status deploy/vexa-vexa-gateway
kubectl -n vexa port-forward svc/vexa-vexa-gateway 8000:8000 &
curl -sf localhost:8000/health
```

## Local k3s smoke (no registry)

```bash
make -C deploy/helm test     # static gate:helm — lint + render assertions, no cluster
make -C deploy/helm smoke    # build 5 images → import into k3s containerd → install → status
make -C deploy/helm down     # uninstall + drop namespace
```

`smoke` needs `sudo` (k3s writes a root-only kubeconfig at `/etc/rancher/k3s/k3s.yaml`) and a local
Docker to build the images. It proves the control plane stands up and `/health` is green.

## Configuration that matters

| Knob | Default | Notes |
|---|---|---|
| `global.imageTag` | `""` | Set to a pinned `YYMMDD-HHMM` tag — overrides every service tag (build-once). |
| `runtime.backend` | `k8s` | `k8s` spawns Pods via RBAC (real cloud); `docker` mounts the host socket (single-node only); `process` runs child processes. |
| `secrets.*` | placeholders | `adminApiToken`, `internalApiSecret`, `transcriptionServiceToken`, `dispatchSigningKey`, `nextauthSecret`, `anthropic*`. Or set `secrets.existingSecretName` (must carry `ADMIN_API_TOKEN`, `INTERNAL_API_SECRET`, `TRANSCRIPTION_SERVICE_TOKEN`, `VEXA_DISPATCH_SIGNING_KEY`, `NEXTAUTH_SECRET`). |
| `postgres.enabled` / `redis.enabled` / `minio.enabled` | `true` | Flip to `false` to use managed backing; then set `database.*` / `redisConfig.*` and a pre-existing `postgres.credentialsSecretName`. |
| `pgbouncer.enabled` | `false` | Transaction pooler for managed Postgres with a fixed slot budget. |
| `terminal.enabled` | `true` | The web UI. Set `terminal.publicUrl` (NEXTAUTH_URL/TERMINAL_URL) when fronted by ingress; add OAuth via `terminal.extraEnv`. |
| `ingress.enabled` | `false` | Fronts the **terminal** by default; set `host`/`className`/`tls`. Add a second path to `gateway` to also expose the raw API. |
| `minio.service.type` | `ClusterIP` | `NodePort` to reach presigned download URLs browser-side on dev clusters. |

## Known boundaries (v0.12)

- **Bot spawn** works on k8s (the bot's config arrives as one env var). **Agent-worker** Pods mount
  the workspace store with **per-mount tenant isolation**: one `subPath` + `readOnly` volumeMount per
  granted workspace against the store PVC (`runtime_kernel/mounts.py:k8s_volume_mounts`) — a worker's
  filesystem contains only its dispatch's workspaces. Multi-node clusters need an **RWX** storage
  class for the store PVC (NFS/Longhorn; k3s `local-path` is RWO-only — single node works), with
  `agentApi.workspaces.accessMode: ReadWriteMany`.
- The `runtime` image bundles `kubectl` for the k8s backend; the docker/process backends ignore it.

## Contracts

This is a composition layer — it owns no service code and consumes none of the `*.v1` schemas
directly (each service vendors its own). It mirrors the [`deploy/compose`](../compose/) env contract.

## Smoke probe — "is this install actually working?"

```bash
make probe SURFACE=helm          # from the repo root; port-forwards if GATEWAY_URL is unset
GATEWAY_URL=http://<node>:<nodePort> make probe SURFACE=helm   # drive a NodePort directly
```

The full-journey smoke (spawn → schedule → boot → join → transcribe → live-view → stop) plus a
one-shot `kubectl logs` sweep of every deployment. Mints its API key through the release secret's
`ADMIN_API_TOKEN` unless `VEXA_API_KEY` is given. See `deploy/helm/probe.sh`.
