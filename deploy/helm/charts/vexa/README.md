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
`postgres/redis.enabled`, `pgbouncer.enabled`, `ingress.*`, `calendar.*` (internal calendar feeds and the
auto-join block list), `meetingApi.transcriptionLanguage` / `meetingApi.transcriptionAllowedLanguages`
(the deployment's default transcription language), `terminal.oidc.*` / `terminal.signinMethods`
(sign-in through your own OIDC provider; guide: [docs.vexa.ai/sign-in-oidc](https://docs.vexa.ai/sign-in-oidc)).

## Identity, the MCP server and the network policy

- **Generated at install.** The gateway's identity keypair (`templates/identity-keys.yaml`): the Ed25519
  private key in the `identity-signing-key` Secret, mounted by the gateway alone, and the public key in
  the `identity-public-key` ConfigMap, derived from it on every render and mounted by agent-api,
  meeting-api and the credential broker, which verify and cannot sign. The Secret is read back on every
  upgrade and kept through an uninstall; set `identity.signingKey` to supply your own (required when
  rendering with `helm template`). `VEXA_MCP_DELEGATION_SECRET` (each agent worker's delegation token,
  verified by admin-api) is generated into the chart Secret; set `secrets.mcpDelegationSecret` to supply
  your own, and with `secrets.existingSecretName` your Secret must carry it.
- **Generated at install: the CRM's fact seal keypair** (`templates/fact-seal-keys.yaml`, with crm and
  flows on). The crm outbox signs each card fact with its own Ed25519 private key (the
  `fact-seal-crm-key` Secret, mounted by crm-outbox alone); flows-api verifies under the public half
  (`crm.pem` in the `fact-verify-keys` ConfigMap) and cannot sign. Same lifecycle as the identity
  keypair; set `factSeal.crm.signingKey`, or `factSeal.crm.existingSecret` with
  `factSeal.crm.publicKey`, when rendering with `helm template`. `secrets.existingSecretName` does
  not carry it.
- **Generated at install: the subject-ticket keypairs** (`templates/subject-ticket-keys.yaml`, with
  flows on). The flows service door forwards a call that acts for a person only with a ticket the
  producer of that person's fact signed (subject-ticket.v1). One Ed25519 keypair per producer, each
  private key a Secret mounted by that producer alone — `subject-ticket-meetings-key` (meeting-api),
  `subject-ticket-agent-key` (agent-api), `subject-ticket-mail-key` (flows-mailbox and flows-worker)
  — and the public halves in the `subject-ticket-verify-keys` ConfigMap (`<issuer>.pem`), mounted by
  the gateway alone. Same lifecycle as the identity keypair; per producer set
  `subjectTickets.<issuer>.signingKey`, or `subjectTickets.<issuer>.existingSecret` with
  `subjectTickets.<issuer>.publicKey`, when rendering with `helm template`.
  `secrets.existingSecretName` does not carry them.
- **One MCP server.** `mcp.enabled` (default on) deploys the assembled MCP service; the gateway relays
  `/mcp` to it and agent-api points every worker's toolbelt at the gateway's `/mcp`. Workers reach it
  only through the gateway.
- **The agent's model route.** `models.runner` (`openai-agent` for vLLM, LiteLLM and other
  OpenAI-compatible endpoints), `models.llmBaseUrl`, `models.llmExtraBody` and `secrets.llmApiKey` are
  set on agent-api and on the runtime, which passes them to each agent worker. The model must do tool
  calling, or each turn ends with the typed fault `model-provider` / `no_tool_calling`. An endpoint on a
  private range goes in `networkPolicy.workloads.worker.extraEgress`. With `global.imageTag`, the worker
  image keeps `runtime.agentWorkerImage`'s repository, so a mirrored registry supplies it. Operator
  page: [`docs/docs/deployment-helm-agent-tools.mdx`](../../../../docs/docs/deployment-helm-agent-tools.mdx).
- **Who reaches agent-api and meeting-api.** `networkPolicy.enabled` (default on) admits agent-api traffic
  from the gateway, the MCP service, the runtime, flows and the terminal, and meeting-api traffic from
  the gateway, agent-api, the MCP service, the runtime and meeting bots. Agent workers reach neither —
  they act through the gateway. Add your own peers with `networkPolicy.agentApi.extraFrom` /
  `networkPolicy.meetingApi.extraFrom`. The policy needs a CNI that enforces NetworkPolicy.
- **The admin account.** `adminApi.adminAccount` (admin-api `VEXA_ADMIN_ACCOUNT`) names the one
  account that writes the company layer `_global`; it has no desk, workspaces or meetings. Use a fresh
  address, roll admin-api, then sign in as that address. Empty means nobody writes `_global`. The old
  `agentApi.globalAdminSubjects` value is gone; the chart no longer renders it.

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
| `cpu` | container `requests.cpu` **and** `limits.cpu`, in millicores (`0.5` → `500m`) | one value per dimension is runtime.v1's shape |
| `meetingBot.cpuLimit` | the bot container's `limits.cpu` only (`2` → `2000m`) | optional; at least `meetingBot.cpu`, which must be set. `""` (the default) ⇒ the limit equals `cpu` |
| `memoryMb` | container `requests.memory` **and** `limits.memory`, in MiB (`2048` → `2048Mi`) | a **hard ceiling** — a workload exceeding it is OOM-killed |
| either, set to `""` | nothing rendered for that class | preserves the optional contract: an unsized Pod, exactly as before |

Request equals limit, so both classes get **Guaranteed** QoS. The shipped defaults are conservative
and sized to fit a small dev cluster — raise `meetingBot.memoryMb` for real meetings rather than
discovering the ceiling as a mid-meeting OOM kill. A caller may also override per workload by
sending `resources` in its `runtime.v1` `WorkloadSpec`; the chart values are the default that
applies when it does not, and a caller's `resources` always set request = limit.

`meetingBot.cpuLimit` above `cpu` lets a bot burst onto a node's idle cores when the browser needs
more than its reservation. Scheduling, preemption and the cluster autoscaler still count only the
request, so bots pack exactly as before. The bot Pod becomes **Burstable**. With memory request =
limit it never uses more memory than it requested, so kubelet node-pressure eviction ranks it as
before; the kernel OOM killer does not — a Burstable container's `oom_score_adj` is
`1000 − 1000 × memory request ÷ node memory` rather than Guaranteed's `−997`, so if a node runs out
of memory before the kubelet evicts anything, a bot is no longer the last process killed.

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

## The model catalog

`models.catalog` is the operator's model catalog (`VEXA_MODEL_CATALOG` on agent-api; `models.v1`
Catalog, ADR-0043): the models people pick per chat. Empty (the default) means no catalog, and every
turn runs on `models.agentModel` and each person's Settings → Models. agent-api refuses a catalog
with anything wrong in it at boot, naming every problem.

| Value | Default | Renders as |
|---|---|---|
| `models.catalog` | `{}` | `VEXA_MODEL_CATALOG` on agent-api, as one JSON value |
| `models.catalogSecrets[].env` | — (required) | the variable a provider's `secret_ref: env:VEXA_MODEL_SECRET_<NAME>` names; must match `VEXA_MODEL_SECRET_[A-Z0-9_]+` or the render fails |
| `models.catalogSecrets[].secretName` | — (required) | the Secret it is read from (`secretKeyRef`). No default, so a catalog key never falls back to the chart's shared admin-token Secret |
| `models.catalogSecrets[].key` | the `env` name | the key in that Secret |

A credential is never written in `models.catalog` itself. Worked examples: `values.yaml` (under
`models:`), the [Model catalog](https://docs.vexa.ai/model-catalog) page, and the
[configuration reference](https://docs.vexa.ai/configuration#agent-inference-bring-your-own).

## Credentials the runtime forwards into spawned Pods

A spawned Pod's container env comes from the dispatch (an agent worker's model route, its unit's
tokens, a bot's constructor) and from the settings the runtime forwards from its own environment.
Any non-empty key the runtime's config contract marks `secret` — and any key the contract does not
declare — never appears as a value in the Pod spec, whichever of the two set it: the runtime puts it
in a Secret of that Pod's own, owned by the Pod so the cluster deletes it with the Pod, and
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

## OpenShift and multi-node clusters

Layer `values-openshift.yaml` (in this chart) over your values and name a ReadWriteMany class with `agentApi.workspaces.storageClassName`. It switches off the hostPath seccomp installer, which restricted Pod Security refuses (install the bot profile with a MachineConfig or the Security Profiles Operator), and makes the agent workspace store ReadWriteMany: on ReadWriteOnce block storage an agent worker on another node waits for the volume and its chat never answers.

## Corporate TLS inspection: add your root CA

If a proxy inspects outbound HTTPS and re-signs it with your own root CA, every Vexa Pod has to trust
that CA, or calls to transcription, the model provider, git hosts, OAuth providers and the meeting
pages the bot opens fail certificate verification. Put the PEM (one or more certificates) in a
ConfigMap in the release namespace and name it:

```bash
oc -n <ns> create configmap corp-root-ca --from-file=ca.crt=<your-root-ca.pem>
helm upgrade --install vexa <chart> ... --set global.extraCA.configMap=corp-root-ca
```

(`global.extraCA.secret` takes a Secret instead; `global.extraCA.key` names the key, default
`ca.crt`.) Every Vexa Deployment and Job, and every meeting-bot and agent-worker Pod the runtime
spawns, then mounts the PEM read-only. An init container writes the image's system bundle plus your
PEM into an emptyDir, and `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, `GIT_SSL_CAINFO`
and `AWS_CA_BUNDLE` point at it, `NODE_EXTRA_CA_CERTS` at your PEM. The bot also adds it to
Chromium's certificate store, which reads no bundle file. It needs no root and no writable root
filesystem, so it runs under OpenShift restricted-v2. Unset, the chart renders exactly as before.

`storage.s3.caBundle`, `terminal.oidc.caBundle`, the flows mail CA and a git provider's own
`caBundle` keep working as before for their own endpoint; when `storage.s3.caBundle` is set,
`AWS_CA_BUNDLE` is left to it.
