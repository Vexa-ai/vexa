# runtime.v1 — the workload lifecycle contract

The published contract between the **control plane** (meeting-api, agent-api) and the **kernel**
(`runtime`). The control plane asks the kernel to run a *workload*; the kernel runs it on Docker / K8s /
a child process and reports its lifecycle. **Mechanism, not policy (P11):** the caller names a
`profile`, `env` and resources. The deployment's profile registry, not the spec, maps a profile to an
image or command and to the profile data every backend applies the same way: the workload's labels,
the network it joins, the runtime settings forwarded into it, and whether the runtime's credential
files are mounted. Nothing in a caller's spec can choose them.

## Transport and authentication
HTTP. **Every route except `GET /health` requires the caller credential**:
`Authorization: Bearer <RUNTIME_API_TOKEN>` (`$defs/CallerCredential`). The runtime, agent-api and
meeting-api hold the same value; no workload receives it. A missing or wrong credential is a **401**
with `WWW-Authenticate: Bearer`. The runtime will not boot without a token, with one shorter than 32
bytes, or with a placeholder published in this repository.

**The callback is signed.** Every `RuntimeEvent` the runtime POSTs to a `callbackUrl` carries
`X-Runtime-Signature: t=<unix seconds>,v2=<hex>` (`$defs/CallbackSignature`): HMAC-SHA256, keyed with
HMAC-SHA256(`RUNTIME_API_TOKEN`, `"vexa-runtime-callback.v2"`), over the signing time, a newline, the
URL the callback is delivered to, a newline, and the event as canonical JSON (keys sorted, no
whitespace, UTF-8). The runtime signs at every delivery attempt, so a retry carries a fresh time. The
token itself never travels to a callback URL. meeting-api, which holds the token, refuses with 401
and moves no meeting a `/runtime/callback` that is unsigned or forged, signed for another URL,
timestamped more than 300 seconds from its clock, or a replay of one it already accepted. This
replaces the `v1=` form (event only): a runtime from before v0.13.2 is refused, as the co-release
already requires.

**A caller of a runtime from before this requirement.** agent-api and meeting-api send the bearer
from the release that introduced it (v0.13.2). Upgrade the runtime together with both callers; a
caller that sends no bearer gets 401 on every workload and schedule call.

## The spec's `env` — what a caller may not set
Keys starting `RUNTIME_K8S_` or `VEXA_WORKSPACE_MOUNT_` (`$defs/RuntimeOwnedEnvPrefix`) belong to the
runtime and are **dropped** from a spec, silently: Pod scheduling and Secret mounts, and the
workspace store's backing, are the runtime's own configuration. When a spec declares a mount set
(`VEXA_MOUNTS` or `VEXA_WORKSPACE_PATH`), the runtime injects `VEXA_WORKSPACE_MOUNT_TARGET` (and
`VEXA_WORKSPACE_MOUNT_SOURCE`, when it has one) itself. A mount set it will not serve is **refused
with 400** before any container, Pod or process exists:

- `VEXA_MOUNTS` that is not a JSON array of objects, each with a `path`;
- a mount `path` not strictly under the workspace store, or `VEXA_WORKSPACE_PATH` outside it;
- a mount whose `source` is not one the runtime was configured to serve (the `_global` tier's path).

## Errors
Every non-2xx answer is `{ "detail": … }` (`$defs/Error`):

| Status | When |
|---|---|
| 400 | unknown profile · a refused mount set (above) · a schedule job without `request.url` or without `execute_at`/`cron` |
| 401 | the caller credential is missing or wrong |
| 404 | unknown workload or job |
| 422 | the body does not parse as the route's shape |
| 429 | the owner's workload quota is full |
| 502 | the backend could not start the workload; its status is already `stopped` / `start_failed` |
| 503 | the scheduler is not wired (`/schedule` routes) |

## The seam — bot and agent are the same thing to the kernel
The meeting **bot** (meetings domain) and the **agent** (agent domain) are *both workloads*, created
through this one contract — they differ only by `profile` and `env`:

| | bot | agent |
|---|---|---|
| `profile` | `meeting-bot` | `agent` |
| `env` | `BOT_CONFIG` (invocation.v1) | scoped token + config |
| git workspace | — | the agent clones/commits its **own** repo via `env` (repo URL + scoped token) |
| reports | lifecycle.v1 → its callback | its own status |

**They never call each other.** They are coupled only by `transcript.v1` (the bot produces it; the agent
consumes it off the bus). The agent's git workspace is its **own** durable memory — not a channel to the
bot, and not something the kernel knows about. The kernel is the only thing that knows docker/k8s/process.
This is why `meetings ⊥ agent` holds (gate:graph) — both depend on `runtime`, neither on the other.

## Lifecycle (the state machine)
```
(create) → starting → running → stopping → stopped → destroyed
                │          │         ▲
                │          └── stop() / idle_timeout / max_lifetime
                └── start_failed ───────────────────┘ (→ stopped, reason=start_failed)

running → stopped directly when the workload exits on its own (reason=completed | failed)
```
- **starting** — provision the container/process, mount the workspace.
- **running** — process up; `ports` bound; the kernel POSTs a `RuntimeEvent` to `callbackUrl`.
- **stopping** — graceful: SIGTERM + a grace period; the workload persists whatever it owns (e.g. the agent `git push`es its commits) before SIGKILL. The kernel never knows *what* is persisted.
- **stopped** — process gone; carries `exitCode` + `stopReason`.
- **destroyed** — resources reclaimed (terminal).

`stopReason`: `completed · stopped · idle_timeout · failed · oom · start_failed · max_lifetime`.

> Two channels, kept separate. **runtime.v1** carries the *container* lifecycle (starting→…→destroyed).
> The workload's *domain* status (the bot's join→active→completed) is a **separate** contract
> (`lifecycle.v1`) the workload emits directly to its own callback — the kernel never interprets it.

## Operations
| Op | In → Out |
|---|---|
| `create` | `WorkloadSpec` → `{ workloadId, state: "starting" }` — **idempotent on `workloadId`** (ADR 0027): while that workload is `starting`/`running`, `create` is a *touch* — it returns the live `WorkloadStatus` unchanged (no respawn, no spec overwrite, no quota charge, no events). Only an absent or exited workload spawns; a re-`create` after self-exit replaces it. |
| `get` | `workloadId` → `WorkloadStatus` |
| `list` | filter? → `WorkloadStatus[]` |
| `stop` | `workloadId, reason?` → `{ state: "stopping" }` (graceful SIGTERM; the workload persists itself) |
| `destroy` | `workloadId` → `{ state: "destroyed" }` (force cleanup) |
| `callback` | the kernel POSTs `RuntimeEvent` to `spec.callbackUrl` on every transition |

## Shapes
Defined in [`runtime.schema.json`](runtime.schema.json) (`$defs`): **WorkloadSpec** (create input),
**WorkloadStatus** (the kernel's view), **RuntimeEvent** (the callback), **Error**, plus the
`RuntimeState`, `StopReason`, `CallerCredential`, `CallbackSignature` and `RuntimeOwnedEnvPrefix`
definitions and the `SignedEventVector` shape. Conforming examples live in [`golden/`](golden/) and are validated by
[`validate.mjs`](validate.mjs) (run by `gate:schema`).

## Status
**Sealed** in `contracts.seal.json` (gate:contract-version). An additive change re-seals with
`pnpm seal:contracts` in a `lane:contract` PR; a breaking one is `runtime.v2`.
