# runtime.v1 — the workload lifecycle contract

The published contract between the **control plane** (meeting-api, agent-api) and the **kernel**
(`runtime`). The control plane asks the kernel to run a *workload*; the kernel runs it on Docker / K8s /
a child process and reports its lifecycle. **Mechanism, not policy (P11):** the kernel knows a `profile`
(an opaque name), `env`, and resources — it does *not* know what a "meeting-bot"
or "agent" *is*. Those are profiles (config), not kernel code.

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
| `create` | `WorkloadSpec` → `{ workloadId, state: "starting" }` |
| `get` | `workloadId` → `WorkloadStatus` |
| `list` | filter? → `WorkloadStatus[]` |
| `stop` | `workloadId, reason?` → `{ state: "stopping" }` (graceful SIGTERM; the workload persists itself) |
| `destroy` | `workloadId` → `{ state: "destroyed" }` (force cleanup) |
| `callback` | the kernel POSTs `RuntimeEvent` to `spec.callbackUrl` on every transition |

## Shapes
Defined in [`runtime.schema.json`](runtime.schema.json) (`$defs`): **WorkloadSpec** (create input),
**WorkloadStatus** (the kernel's view), **RuntimeEvent** (the callback), plus the `RuntimeState` and
`StopReason` enums. Conforming examples live in [`golden/`](golden/) and are validated by
[`generate.mjs`](generate.mjs) (run by `gate:schema`).

## Status
**`lane:contract` — under review.** This is the first published contract; the shape is up for your
reaction before the remaining seams (`transcript`, `lifecycle`, `acts`, `invocation`, `workspace`) are
drafted. Not committed until reviewed (the human gate).
