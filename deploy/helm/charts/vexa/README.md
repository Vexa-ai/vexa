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

## Sizing the spawned bot and agent-worker Pods

The `runtime` creates the meeting-bot and agent-worker Pods dynamically. Namespace policy commonly
**requires every container to declare CPU and memory requests *and* limits** — a `ResourceQuota`
naming `requests.cpu`/`limits.memory`, or a restricted OpenShift project. A Pod that declares none
is rejected at admission, and the meeting or dispatch never starts.

`runtime.workloadResources` sizes the two classes **independently** (this is *not*
`runtime.resources`, which sizes the runtime Deployment itself):

```yaml
runtime:
  workloadResources:
    meetingBot:   { cpu: 1,   memoryMb: 2048 }   # Chromium + capture pipeline
    agentWorker:  { cpu: 0.5, memoryMb: 1024 }   # code harness
```

| Value | Renders as | Notes |
|---|---|---|
| `cpu` | container `requests.cpu` **and** `limits.cpu`, in millicores (`0.5` → `500m`) | one value per dimension is runtime.v1's shape; there is no separate request/limit knob |
| `memoryMb` | container `requests.memory` **and** `limits.memory`, in MiB (`2048` → `2048Mi`) | a **hard ceiling** — a workload exceeding it is OOM-killed |
| either, set to `""` | nothing rendered for that class | preserves the optional contract: an unsized Pod, exactly as before |

Request equals limit, so both classes get **Guaranteed** QoS. The shipped defaults are conservative
and sized to fit a small dev cluster — raise `meetingBot.memoryMb` for real meetings rather than
discovering the ceiling as a mid-meeting OOM kill. A caller may also override per workload by
sending `resources` in its `runtime.v1` `WorkloadSpec`; the chart values are the default that
applies when it does not.

Enforcement is **Kubernetes-only**. The docker and process backends accept the same resource intent
and do not act on it — they have no admission controller to satisfy, and no parity is claimed.

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

## Testing #1005 + #1006 on your own cluster (validation branch)

`deploy/helm/values-oenb-validation.yaml` is a ready-to-edit values file for validating both fixes
on a quota-controlled, mirror-only cluster. Replace every `REPLACE-ME.registry.internal` with your
registry and install with `-f`.

**Point it at your own registry.** The #1005 fix is Python under `core/runtime/`, so it exists only
inside a **rebuilt runtime image** — no published tag contains it. Build it from this branch, push it
to your registry, and set `runtime.image.repository` / `.tag` to that copy:

```bash
docker build -t <your-registry>/vexa/v012-runtime:<your-tag> core/runtime
docker push     <your-registry>/vexa/v012-runtime:<your-tag>
```

#1006 is chart-only, so it needs no rebuild — it works against images you already mirror.

> ### ⚠️ `global.imageTag` silently overrides the runtime tag
>
> The runtime Deployment renders
> `image: "{{ .Values.runtime.image.repository }}:{{ .Values.global.imageTag | default .Values.runtime.image.tag }}"`.
>
> **If `global.imageTag` is set, it wins over `runtime.image.tag` — with no error and no warning.**
> Setting it to a release tag (the natural move on a mirror-only install, since it pins every other
> image at once) gives you the **old runtime without the #1005 fix** while your values file appears
> to pin the fixed one. Every Pod goes Running, your spawned bots still declare no resources, the
> quota still rejects them, and you conclude the fix is broken — having never run it.
>
> Either leave `global.imageTag` **unset**, or set it to **the same tag you built the runtime with**.
> Then confirm what you actually got, before trusting any spawn:
>
> ```bash
> kubectl -n <ns> get deploy <release>-vexa-runtime \
>   -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
> ```
>
> (Tracked upstream as [#1001](https://github.com/Vexa-ai/vexa/issues/1001) — a digest helper for
> long-running Deployments — which is still open.)

Confirm the sizing reached the runtime too — four entries, all non-empty:

```bash
kubectl -n <ns> get deploy <release>-vexa-runtime -o json \
  | jq '.spec.template.spec.containers[0].env[]
        | select(.name|test("^RUNTIME_(BOT|AGENT_WORKER)_(CPU|MEMORY_MB)$"))'
```

Empty strings mean the chart values did not land, and the spawned Pods will declare nothing —
exactly the pre-#1005 behaviour.

**The `minio-init` hook Job is the trap that bites before either fix does.** It is a post-install
hook, so on a quota-controlled namespace it is rejected, retried forever, and `helm install --wait`
**hangs** while every Deployment sits Running — a failure that looks nothing like a quota problem.
Size it via `minio.mc.resources` (defaulted in this branch, absent from `main`).

## Validate (no cluster)

```bash
helm lint .
helm template vexa . -n vexa -f values-test.yaml
make -C ../.. test          # deploy/helm: lint + render assertions + the mirror-only invariant
```

`tests/test_mirror_only.sh` renders `tests/values-mirror-only.yaml` — every image pointed at one
unresolvable internal registry — and asserts that no container image, hook image or runtime-spawn
reference falls outside it. Run it after any change that touches an image reference.
