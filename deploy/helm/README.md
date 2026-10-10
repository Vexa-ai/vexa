# deploy/helm — the v0.12 control-plane chart (Kubernetes)

The `helm` target of the lite/compose/helm trio: the full v0.12 stack as a Kubernetes release —
the control plane **gateway · admin-api · meeting-api · runtime · agent-api**, the **terminal** web
UI, and infra (`postgres:17` · `valkey:8`); recordings go to **your** S3 bucket (`storage.s3`). The **terminal** is
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
# 1. Pin the image tag your build produced (build-once promotion), fill secrets, and point
#    storage.s3 at your bucket (vexa-s3 = a Secret with AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY).
helm upgrade --install vexa deploy/helm/charts/vexa -n vexa --create-namespace \
  --set global.imageTag=YYMMDD-HHMM \
  --set secrets.adminApiToken=$ADMIN_TOKEN \
  --set secrets.internalApiSecret=$INTERNAL_API_SECRET \
  --set secrets.transcriptionServiceToken=$STT_TOKEN \
  --set storage.s3.endpoint=https://s3.eu-central-1.amazonaws.com \
  --set storage.s3.bucket=my-vexa-recordings \
  --set storage.s3.existingSecret=vexa-s3 \
  --wait --timeout 10m

# 2. Watch it come up, then probe the front door.
kubectl -n vexa rollout status deploy/vexa-vexa-gateway
kubectl -n vexa port-forward svc/vexa-vexa-gateway 8000:8000 &
curl -sf localhost:8000/health
```

## Local k3s smoke (no registry)

```bash
make -C deploy/helm test     # static gate:helm — lint + render assertions, no cluster
make -C deploy/helm smoke    # build 5 images → import into k3s containerd → S3 fixture → install → status
make -C deploy/helm down     # uninstall + drop namespace
```

`smoke` installs with `values-test.yaml`, whose recordings go to the test-only S3 fixture in
[`tests/s3-fixture.yaml`](tests/s3-fixture.yaml); `make s3-fixture` starts it first.

`smoke` needs `sudo` (k3s writes a root-only kubeconfig at `/etc/rancher/k3s/k3s.yaml`) and a local
Docker to build the images. It proves the control plane stands up and `/health` is green.

## Configuration that matters

| Knob | Default | Notes |
|---|---|---|
| `global.imageTag` | `""` | Set to a pinned `YYMMDD-HHMM` tag — overrides every service tag (build-once). |
| `runtime.backend` | `k8s` | `k8s` spawns Pods via RBAC (real cloud); `docker` mounts the host socket (single-node only); `process` runs child processes. |
| `secrets.*` | placeholders | `adminApiToken`, `internalApiSecret`, `transcriptionServiceToken`, `anthropic*`. `adminApiToken` is never a value published in this repository (its old default `CHANGE_ME` is refused); `dispatchSigningKey` and `nextauthSecret` take 32+ bytes and never a published value; they, `adminApiToken`, `runtimeApiToken`, `redisPassword` and `mcpDelegationSecret` keep the release Secret's value when empty, or are generated at install. Or set `secrets.existingSecretName` (must carry `ADMIN_API_TOKEN` (never a published value: the services refuse to boot on one), `INTERNAL_API_SECRET`, `RUNTIME_API_TOKEN` (32+ bytes), `REDIS_PASSWORD` (32+ URL-safe characters, with the chart's redis), `TRANSCRIPTION_SERVICE_TOKEN`, `VEXA_DISPATCH_SIGNING_KEY` (not `dev-dispatch-signing-key`, which agent-api refuses), `VEXA_MCP_DELEGATION_SECRET`, `NEXTAUTH_SECRET`). |
| `storage.s3.endpoint` / `bucket` / `existingSecret` | required | Your S3 bucket for recordings and a Secret with its key pair (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`). Optional: `region`, `forcePathStyle`, `caBundle`. No object store runs in the chart; `minio.enabled: true` is refused. |
| `postgres.enabled` / `redis.enabled` | `true` | Flip to `false` to use managed backing; then set `database.*` / `redisConfig.*` and a pre-existing `postgres.credentialsSecretName`. |
| `pgbouncer.enabled` | `false` | Transaction pooler for managed Postgres with a fixed slot budget. |
| `terminal.enabled` | `true` | The web UI. Set `terminal.publicUrl` (NEXTAUTH_URL/TERMINAL_URL) when fronted by ingress; add OAuth via `terminal.extraEnv`. |
| `ingress.enabled` | `false` | Fronts the **terminal** by default; set `host`/`className`/`tls`. Add a second path to `gateway` to also expose the raw API. |
| `agentApi.workspaces.accessMode` | `ReadWriteOnce` | The **one** workspace store, and it has two mounters, not one: agent-api holds the claim for its whole lifetime, and **every worker Pod the runtime spawns mounts the same PVC** (`runtime_kernel/k8s_backend.py` builds the Pod from the runtime's own `VEXA_WORKSPACE_MOUNT_SOURCE`, the claim name the chart gives the runtime; a spec's copy of the key is dropped). RWO binds a volume to one **node**, so the moment a worker is scheduled anywhere else it fails Multi-Attach and the dispatch never starts — and agent-api's own rollout has to be `Recreate` for the same reason. Single-node (k3s `local-path`) is fine on RWO. **Multi-node needs `ReadWriteMany`** plus a storage class that supports it (NFS/Longhorn; on OpenShift typically ODF CephFS) — set both, or keep every worker pinned to agent-api's node. |
| `networkPolicy.enabled` | `true` | Renders the fences: ingress to agent-api, meeting-api and the runtime (agent-api and meeting-api only), and the egress of every spawned workload. The credential broker's own fence (agent-api and the terminal only) is `credentialBroker.networkPolicy.enabled`. `agentApi.extraFrom` / `meetingApi.extraFrom` / `runtime.extraFrom` admit extra ingress sources. Inert without a CNI that enforces NetworkPolicy. |
| `networkPolicy.defaultDeny.enabled` | `true` | A namespace-wide default-deny in both directions, with explicit allows for the chart's Pods (`templates/networkpolicy-namespace.yaml`). It also denies every Pod in the namespace that is not the chart's: **install Vexa in a namespace of its own**, or give those Pods their own policies. `networkPolicy.controlPlane.extraIngress` admits a scraper; `networkPolicy.controlPlane.egressExcept` keeps the control plane off the cloud metadata range (empty it if a Pod authenticates through the metadata server); `networkPolicy.postgres.extraFrom` adds callers of the bundled database. |
| `networkPolicy.workloads.worker.extraEgress` / `.bot.extraEgress` | `[]` | Spawned Pods take no inbound connection and reach only what their class needs (the `vexa.role` label the runtime stamps from the profile). A **worker** reaches the gateway, redis, flows-api (when deployed), DNS and the internet; a **bot** reaches meeting-api, redis, DNS and the internet. Add an in-cluster dependency the chart does not render (an STT, an LLM endpoint, an S3) here, per class, as NetworkPolicy egress rules. |
| `networkPolicy.workloads.privateCidrs.ipv4` / `.ipv6`, `networkPolicy.workloads.ipv6` | RFC 1918, CGNAT, link-local, loopback; ULA, link-local, loopback | The ranges a workload may not reach except through the rules above: Pod and Service networks, nodes and the cloud metadata endpoint sit in them. Add your cluster's ranges if they fall outside these. `ipv6: false` leaves out the IPv6 internet rule on a single-stack cluster. |
| `global.securityContext.deliver` | `true` | Whether the chart delivers a `securityContext` at all — pod-level and container-level, on all 8 workloads. Keep `true` on plain Kubernetes: PSA-restricted namespaces *validate* these fields and refuse a spec without them. Set **`false` on OpenShift**: `restricted-v2` *injects* them (random UID, `runAsNonRoot`, drop ALL, `RuntimeDefault`, `fsGroup`) and is more likely to reject a spec that supplies its own. The vexa-delivery OpenShift provider profile sets it false. |

## Rendering with `helm template` (GitOps)

Values the chart generates (the runtime caller token, the dispatch signing key, the Redis and database
passwords, the sign-in secret, the delegation secret, the gateway's signing key and the credential
broker's keys) are kept across `helm upgrade` by reading the release's own Secrets. A tool that renders
with `helm template` cannot read them and generates new ones on every render, so each sync would change
them under running services. Supply each from a Secret you manage:

| Value | Secret it comes from |
|---|---|
| `secrets.existingSecretName` | the chart's shared Secret: besides `ADMIN_API_TOKEN`, `INTERNAL_API_SECRET` and `TRANSCRIPTION_SERVICE_TOKEN`, it carries `RUNTIME_API_TOKEN`, `REDIS_PASSWORD`, `NEXTAUTH_SECRET`, `VEXA_MCP_DELEGATION_SECRET` and `VEXA_DISPATCH_SIGNING_KEY` |
| `credentialBroker.existingSecret` | the broker's `agent.key`, `human.key`, `git.key` and `store.key`. Losing `store.key` makes every stored credential unreadable |
| `identity.existingSecret` + `identity.publicKey` | the gateway's signing key as `signing-key.pem`, and its PEM public key in values (a render cannot derive it from a Secret it cannot read). Or `identity.signingKey` |
| `database.existingSecret` | keys `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`; the chart renders no credentials Secret and never rotates this one. Or `database.password` |
| `flows.existingSecret`, or `secrets.existingSecretName` with `flows.apiKey` empty | flows' operator key `VEXA_FLOWS_API_KEY`. The chart does not generate it; from either Secret, flows-api, flows-worker, flows-mailbox and the MCP edge read it by name and the chart's flows Secret leaves it out, so `flows.enabled` needs no key in values. Setting `flows.apiKey` and `flows.existingSecret` together is refused |

## Known boundaries (v0.12)

- **Bot spawn** works on k8s (the bot's config arrives as one env var). **Agent-worker** Pods mount
  the workspace store with **per-mount tenant isolation**: one `subPath` + `readOnly` volumeMount per
  granted workspace against the store PVC (`runtime_kernel/mounts.py:k8s_volume_mounts`) — a worker's
  filesystem contains only its dispatch's workspaces. Multi-node clusters need an **RWX** storage
  class for the store PVC (NFS/Longhorn; k3s `local-path` is RWO-only — single node works), with
  `agentApi.workspaces.accessMode: ReadWriteMany`.
- **Bundled Postgres cannot run under an arbitrary UID — use an external database on OpenShift.**
  `postgres.image` is the official `postgres:17-alpine` (`values.yaml`), whose entrypoint runs
  `initdb` as the baked-in `postgres` user and needs a passwd entry for whatever UID it is given.
  OpenShift's `restricted-v2` SCC assigns a random per-namespace UID that exists in no
  `/etc/passwd`, so the bundled all-in-one Postgres does not start there. This is a known
  limitation of the convenience path, not of the chart: **set `postgres.enabled: false`** and
  point `database.*` at a managed/operator-run Postgres (CloudNativePG or the Crunchy operator
  in most OpenShift estates), with `postgres.credentialsSecretName` naming a pre-existing
  Secret; add `pgbouncer.enabled: true` if that database has a fixed connection budget.
  Swapping in a UID-agnostic image (bitnami / Red Hat `postgresql`) would also work and is
  deliberately **not** done here — changing the database image under existing installs is a
  data-durability decision, not a packaging one.
- **Do not add `fsGroup` (or `runAsUser` / `runAsGroup`) to get a volume writable on OpenShift.**
  The chart sets none of them anywhere, and that is deliberate: `restricted-v2` assigns an
  `fsGroup` from the namespace's own range and *rejects* any value outside it
  (`MustRunAsRange`), so a hand-set `fsGroup` turns a working install into an admission
  failure. Let admission supply it. Where a volume still comes up unwritable, the fix belongs
  in the image (a `HOME`/data dir writable by any UID) or in the storage class, never in a
  securityContext this chart delivers — see `global.securityContext.deliver` above.
- The `runtime` image bundles `kubectl` for the k8s backend; the docker/process backends ignore it.
- **`TRANSCRIPTION_MODEL` is not values-plumbed yet** (#522 ships the env on compose + Lite): to
  point k8s bots at a validating STT backend (Groq/vLLM), add the env to the meeting-api (and
  terminal) deployment via `extraEnv` for now; first-class `transcription.model` values plumbing is
  a declared follow-up.

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
