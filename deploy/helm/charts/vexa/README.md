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

## Disruption budgets and single-replica components

The four stateless components ship with a PodDisruptionBudget enabled, paired with `replicaCount: 2`.
The budgets are expressed as **`maxUnavailable: 1`**, not `minAvailable: 1`, and the template enforces
that independently: **below two replicas it emits `maxUnavailable: 1` whatever the values say.**

This matters the moment a component drops to one replica, which quota-constrained clusters routinely
force:

| Budget | at `replicaCount: 2` | at `replicaCount: 1` |
|---|---|---|
| `minAvailable: 1` | 1 disruption allowed | **0 allowed — `kubectl drain` hangs forever** |
| `maxUnavailable: 1` | 1 disruption allowed | 1 allowed — the node drains |

A `minAvailable: 1` budget on a one-replica Deployment buys no availability (there is no peer to fail
over to) and costs the cluster operator the ability to cordon a node at all. Reported by an operator
running this chart on a quota-controlled OpenShift cluster; previously the only remedy was to disable
the PDB by hand.

At three or more replicas `maxUnavailable: 1` is *stricter* than `minAvailable: 1` — one eviction at a
time rather than N−1. Set `podDisruptionBudgets.<component>.minAvailable` explicitly to choose the
other trade; it is honoured at two replicas and above, and overridden below that.

Separate from [#990](https://github.com/Vexa-ai/vexa/issues/990), which is about a PDB rendered for a
*disabled* component and therefore selecting zero pods.

## Security context on spawned workloads

`global.securityContext` / `global.podSecurityContext` apply to the chart's own Deployments. The Pods
the runtime **spawns** with `runtime.backend: k8s` (meeting bots, agent workers) are bare `kubectl run`
Pods and inherit nothing from the runtime Deployment — they carry **no security context at all**. On a
namespace enforcing OpenShift's `restricted-v2` SCC or the upstream Pod Security *restricted* profile,
that is the likeliest admission rejection.

`runtime.workloadSecurityContext` supplies one. Both halves default to empty, and empty means the
field is omitted entirely — leave them alone and the spawned Pod spec is byte-identical to before this
knob existed.

```yaml
runtime:
  workloadSecurityContext:
    pod:                                  # a PodSecurityContext, passed through verbatim
      runAsNonRoot: true
      seccompProfile: { type: RuntimeDefault }
    container:                            # a container SecurityContext, passed through verbatim
      allowPrivilegeEscalation: false
      capabilities: { drop: ["ALL"] }
```

**Read this before setting `runAsNonRoot` or `runAsUser`.** No Dockerfile in this repository contains a
`USER` directive — 0 of 15, checkable with `git grep -nE '^\s*USER ' -- '*Dockerfile*'` — so every image
starts as its base image's default UID, which is root. Whether the bot and agent-worker images can run
as an **arbitrary** non-root UID is **untested**: the bot image runs Chromium under Xvfb and PulseAudio
and writes to `/app`, `/opt/hf-cache` and `/tmp`; the agent-worker runs a Claude Code harness over a
mounted workspace. Tracked at [#1102](https://github.com/Vexa-ai/vexa/issues/1102) — the answer needs a
real image on a real restricted cluster, not a values change. That is precisely why the default here is
absent rather than hardened-looking.

The values cross the port as `RUNTIME_K8S_POD_SECURITY_CONTEXT` / `RUNTIME_K8S_CONTAINER_SECURITY_CONTEXT`
(JSON) on the runtime Deployment, the same way `RUNTIME_K8S_TOLERATIONS` / `RUNTIME_K8S_NODE_SELECTOR`
do. Malformed JSON fails loud at spawn rather than silently dropping the constraint.

## Validate (no cluster)

```bash
helm lint .
helm template vexa . -n vexa -f values-test.yaml
```
