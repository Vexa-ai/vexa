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

## Spreading replicas across nodes

`replicaCount > 1` alone buys rolling-update safety, not availability — the scheduler may place
every replica on one node, so losing that node takes the whole component down. Add pod topology
spread to force replicas apart. `global.topologySpreadConstraints` applies to **every** component
(gateway · admin-api · meeting-api · runtime · agent-api · terminal); when a constraint omits
`labelSelector`, the chart injects **that component's own pod selector**, so one block means
"spread each component's own replicas":

```yaml
global:
  topologySpreadConstraints:
    - maxSkew: 1
      topologyKey: kubernetes.io/hostname
      whenUnsatisfiable: ScheduleAnyway   # best-effort — small/single-node clusters still schedule
```

Override per component with `<component>.topologySpreadConstraints` (same shape, wins over the
global default for that component only):

```yaml
gateway:
  topologySpreadConstraints:
    - maxSkew: 1
      topologyKey: topology.kubernetes.io/zone
      whenUnsatisfiable: ScheduleAnyway
```

Provide your own `labelSelector` in a constraint to opt out of the automatic injection. Empty
default (the shipped value) renders nothing — single-node / k3s installs are unaffected. Use
`ScheduleAnyway`, not `DoNotSchedule`, unless you can guarantee enough nodes, or pods stay Pending.

## Image references

Every image in this chart resolves by **one rule**, in this order — nothing else participates:

| # | Value | Result |
|---|---|---|
| 1 | `<x>.image` / `<x>Image` — an explicit **full reference** | used **verbatim** |
| 2 | `<x>.digest` / `<x>ImageDigest` — `sha256:<64 lowercase hex>` | `<repository>@<digest>` |
| 3 | `<x>.repository` + `<x>.tag` | `<repository>:<tag>` |

**An explicit full reference wins. `global.imageTag` never rewrites it** — it only supplies the tag
on leg 3, for the images that have no explicit identity. That is the deterministic precedence a
private-mirror install depends on; before v0.12.19 two of the runtime's spawned references ignored
it (see [#1006](https://github.com/Vexa-ai/vexa/issues/1006)).

The chart **fails closed** rather than guessing. `helm template` errors, and nothing renders, when:

- an explicit full reference **and** a digest are both set on the same image (two identities, no
  defensible winner);
- a digest is not exactly `sha256:` + 64 **lowercase** hex — truncated, uppercase or non-`sha256`
  is refused, never quietly downgraded to a mutable tag;
- a full reference contains whitespace;
- the repository is empty on legs 2–3, or the tag is empty on leg 3.

### The runtime-spawned images

Three images are **not containers in any rendered manifest**. The chart hands them to the runtime
as env (`BROWSER_IMAGE`, `AGENT_IMAGE`, `AGENT_WORKER_IMAGE`) and the runtime creates those Pods
later. `kubectl get deploy -o yaml` will never name them, and a mirror that holds only what the
manifests show will fail at the first meeting, not at install:

| Env | Explicit reference | Digest | Composed default |
|---|---|---|---|
| `BROWSER_IMAGE` (the bot, ~3.6 GB) | `runtime.browserImage` | `runtime.browserImageDigest` | `runtime.browserImageRepository` + `runtime.browserImageTag` |
| `AGENT_IMAGE` | `runtime.agentImage` | `runtime.agentImageDigest` | `runtime.agentImageRepository` + `runtime.agentImageTag`, both defaulting to `agentApi.image.*` |
| `AGENT_WORKER_IMAGE` | `runtime.agentWorkerImage` | `runtime.agentWorkerImageDigest` | `runtime.agentWorkerImageRepository` + `runtime.agentWorkerImageTag` |

The post-install **`minio-init` hook Job** is the fourth easily-missed pull: `minio.mc.reference` /
`minio.mc.digest` / `minio.mc.image.repository` + `minio.mc.image.tag`. It is deliberately **not** wired to
`global.imageTag` — it is a third-party image, not part of the Vexa release set.

```yaml
# A mirror-only install pinning exact bytes.
global:
  imageTag: v0.12.19
  imagePullSecrets: [{ name: internal-registry-pull }]
runtime:
  image: { repository: registry.example.internal/vexa/v012-runtime }
  browserImage: "registry.example.internal/vexa/vexa-bot@sha256:<64 hex>"
  agentImage:   "registry.example.internal/vexa/v012-agent-api:v0.12.19"
  agentWorkerImageRepository: registry.example.internal/vexa/v012-agent-worker
  agentWorkerImageDigest: "sha256:<64 hex>"
minio:
  image: { repository: registry.example.internal/mirror/minio, tag: latest }
  mc:    { digest: "sha256:<64 hex>", image: { repository: registry.example.internal/mirror/mc } }
```

## Validate (no cluster)

```bash
helm lint .
helm template vexa . -n vexa -f values-test.yaml
make -C ../.. test          # deploy/helm: lint + render assertions + the mirror-only invariant
```

`tests/test_mirror_only.sh` renders `tests/values-mirror-only.yaml` — every image pointed at one
unresolvable internal registry — and asserts that no container image, hook image or runtime-spawn
reference falls outside it. Run it after any change that touches an image reference.
