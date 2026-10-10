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

## Identity, the MCP server and the network policy

- **Generated at install.** The gateway's identity keypair (`templates/identity-keys.yaml`): the Ed25519
  private key in the `identity-signing-key` Secret, mounted by the gateway alone, and the public key in
  the `identity-public-key` ConfigMap, derived from it on every render and mounted by agent-api,
  meeting-api and the credential broker, which verify and cannot sign. The Secret is read back on every
  upgrade and kept through an uninstall; set `identity.signingKey` to supply your own (required when
  rendering with `helm template`). `VEXA_MCP_DELEGATION_SECRET` (each agent worker's delegation token,
  verified by admin-api) is generated into the chart Secret; set `secrets.mcpDelegationSecret` to supply
  your own, and with `secrets.existingSecretName` your Secret must carry it.
- **One MCP server.** `mcp.enabled` (default on) deploys the assembled MCP service; the gateway relays
  `/mcp` to it and agent-api points every worker's toolbelt at the gateway's `/mcp`.
- **Who reaches agent-api and meeting-api.** `networkPolicy.enabled` (default on) admits agent-api traffic
  from the gateway, the MCP service, the runtime, flows and the terminal, and meeting-api traffic from
  the gateway, agent-api, the MCP service, the runtime and meeting bots. Agent workers reach neither —
  they act through the gateway. Add your own peers with `networkPolicy.agentApi.extraFrom` /
  `networkPolicy.meetingApi.extraFrom`. The policy needs a CNI that enforces NetworkPolicy.

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

## The meeting bots' browser sandbox

A meeting bot's Chromium renders pages nobody here controls. Chromium's sandbox (each renderer in
its own user, PID and network namespace, under seccomp-bpf) is what keeps a compromised page inside
its renderer, and it is built on **user namespaces**, which the container runtime's default seccomp
profile refuses. So, with `runtime.botSandbox.enabled` (the default):

- bot Pods run as a non-root uid (`runAsNonRoot`; the bot image's own, or the one OpenShift assigns)
  under a **Localhost** seccomp profile, `runtime.botSandbox.localhostProfile`
  (`vexa/seccomp-userns.json`): Docker Engine's default profile with one rule added, letting the
  bot's container create user namespaces. Agent workers and the chart's own Pods keep
  `RuntimeDefault`;
- a DaemonSet (`<release>-bot-seccomp`) writes that file to the kubelet's seccomp root on every node
  bots may land on (it follows `runtime.workloadScheduling.meetingBot` placement), copied from the
  runtime image. It runs as root only to write that root-owned directory: no capability, no
  privilege escalation, a read-only root filesystem, no API token. It needs a namespace that admits
  a `hostPath` volume (Pod Security `privileged`).

Each bot logs `Chromium runs with its sandbox`, or why it runs without (as root; or no usable
sandbox, e.g. under `RuntimeDefault` with `botSandbox.enabled=false`).

**OpenShift.** `restricted-v2` admits only `runtime/default` seccomp and no `hostPath`, so:

1. Install the profile on the nodes yourself: a `MachineConfig` writing
   `/var/lib/kubelet/seccomp/vexa/seccomp-userns.json` (the file is `runtime_kernel/seccomp-userns.json`
   in the runtime image), or the Security Profiles Operator with a `SeccompProfile` of that content —
   then set `runtime.botSandbox.localhostProfile` to the path it installs
   (`operator/<namespace>/<name>.json`). Set `runtime.botSandbox.installer.enabled=false`.
2. Give the ServiceAccount the bot Pods run as (the namespace's `default`, unless the runtime is
   configured otherwise) an SCC like `restricted-v2` whose `seccompProfiles` also lists
   `localhost/<that path>`. Bots keep `restricted-v2`'s random UID, `allowPrivilegeEscalation: false`
   and no capabilities; the bot image runs under any UID (`HOME=/tmp`).
3. RHCOS allows unprivileged user namespaces by default (`user.max_user_namespaces` > 0).

Without these, set `runtime.botSandbox.enabled=false`: bots then run under `runtime/default`, and
their browsers run unsandboxed (said in each bot's log).

## Credentials the runtime forwards into spawned Pods

The runtime forwards a profile's settings from its own environment into each spawned Pod (an agent
worker's model, its caps, its provider keys). A key the runtime's config contract marks `secret`
(and any key the contract does not declare) never appears as a value in the Pod spec: the runtime
puts it in a Secret of that Pod's own, owned by the Pod so the cluster deletes it with the Pod, and
the container reads it by `secretKeyRef`. The runtime's Role may create Secrets and nothing else
with them (no read, list or delete); each Secret is named per Pod incarnation. The Pod's container
starts once its Secret exists, a moment after the Pod is created.

## Redis never evicts

The chart's Redis holds security state, not only a cache: identity admits a worker's delegation
token only while `vexa:delegation:live:<jti>` exists and no `vexa:delegation:revoked:<jti>` does.
An eviction policy would drop those keys under memory pressure, cutting a live worker's tools or
(before the live record existed) reviving a revoked token. So `redis.maxmemoryPolicy` is
`noeviction`, and any other value fails the render.

When Redis is full it refuses writes (`OOM command not allowed`) instead of dropping keys. agent-api
reports each refused write as a typed failure: a token it cannot record is withheld (the worker
starts without its tools), and a revocation deletes the live record first, which a full Redis still
allows, so a token is refused even when its revocation key cannot be written. `redis.maxmemory`
(768mb by default) sits below `redis.resources.limits.memory` (1Gi) so Redis refuses before the
kernel kills it; raise the two together.

## Validate (no cluster)

```bash
helm lint .
helm template vexa . -n vexa -f values-test.yaml
```

`values-test.yaml` points `storage.s3` at the test-only S3 fixture in
[`../../tests/s3-fixture.yaml`](../../tests/s3-fixture.yaml); an install from it needs that fixture
running first (the file's header has the commands).
