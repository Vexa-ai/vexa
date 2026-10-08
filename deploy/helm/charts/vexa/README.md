# vexa — v0.12 control-plane Helm chart

Deploys the full v0.12 stack to Kubernetes: the control plane **gateway · admin-api · meeting-api ·
runtime · agent-api**, the **terminal** web UI, and infra (`postgres` · `redis`). Recordings go to
**your** S3-compatible bucket (`storage.s3`, required); the chart runs no object store. The
`runtime` spawns the bot and agent-worker as on-demand Pods (`RUNTIME_BACKEND=k8s`, under the
chart's ServiceAccount/RBAC); they are not long-running services.

```
            ┌──────────┐
  client ──>│ gateway  │──> admin-api ──┐
            └────┬─────┘                ├─> postgres
                 └────> meeting-api ────┘
                          │  └─> your S3 bucket (recordings; storage.s3)
                          └─> runtime ──(kubectl run)──> bot Pod / agent-worker Pod
            agent-api ──> runtime                        redis (streams/pubsub)
```

## Install

```bash
# a Secret with the key pair for your bucket, in the release namespace
kubectl -n vexa create secret generic vexa-s3 \
  --from-literal=AWS_ACCESS_KEY_ID=… --from-literal=AWS_SECRET_ACCESS_KEY=…
helm upgrade --install vexa . -n vexa --create-namespace \
  --set global.imageTag=YYMMDD-HHMM \
  --set secrets.adminApiToken=$ADMIN_TOKEN \
  --set secrets.internalApiSecret=$INTERNAL_API_SECRET \
  --set storage.s3.endpoint=https://s3.eu-central-1.amazonaws.com \
  --set storage.s3.bucket=my-vexa-recordings \
  --set storage.s3.existingSecret=vexa-s3
```

The render refuses to proceed without `storage.s3.endpoint`, `bucket` and `existingSecret`, and
refuses a leftover `minio.enabled: true` from a release that ran the removed built-in MinIO — see
the upgrade steps at
[docs.vexa.ai/deployment-kubernetes](https://docs.vexa.ai/deployment-kubernetes#upgrading-from-the-built-in-minio).

See [`../../README.md`](../../README.md) for the cookbook (local k3s smoke, managed backing,
ingress) and the values table. Key knobs: `global.imageTag`, `runtime.backend`
(`k8s`|`docker`|`process`), `secrets.*` (or `secrets.existingSecretName`), `storage.s3.*`,
`postgres/redis.enabled`, `pgbouncer.enabled`, `ingress.*`.

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

## Validate (no cluster)

```bash
helm lint .
helm template vexa . -n vexa -f values-test.yaml
```

`values-test.yaml` points `storage.s3` at the test-only S3 fixture in
[`../../tests/s3-fixture.yaml`](../../tests/s3-fixture.yaml); an install from it needs that fixture
running first (the file's header has the commands).
