# runtime — the kernel (isolated spawn + the scheduler) (Python)

## Purpose
The core **kernel**: it spawns and supervises isolated workloads through the `runtime.v1`
lifecycle over a pluggable Backend (process / Docker / K8s), and runs a redis-backed `Scheduler`
that holds `schedule.v1` HTTP-call jobs in a sorted set and HTTP-POSTs them when due. **Mechanism,
not policy (P11):** a `profile` is an opaque name — the kernel knows docker/k8s/process, not what a
"bot" or "agent" *is*. What a kind of workload is given beyond its spec is **profile data**
(`profiles.Runnable`): its labels, the runtime setting naming the network it joins, the runtime
settings forwarded into it, the credential files it receives and where (`credential_files`,
`credential_env`), the host groups a process-backend child joins (`process_groups`), and whether
it may create user namespaces (`user_namespaces`: a meeting bot, for its browser's sandbox). Every
backend applies that data the same way; only the deployment registry (`default_registry`, the
`meeting-bot` and `agent` profiles) fills it in — it is the one place that turns the operator's
`HOST_CLAUDE_CREDENTIALS` / `HOST_CLAUDE_DIR` / `HOST_CODEX_CREDENTIALS` into files a harness reads.
One residue: the docker backend still renames an `agent-…` workload's container to `worker-…` and
labels its kind (`_worker_naming`), which keys on agent-api's id scheme. Python because this is the runtime/tooling ecosystem
and the control plane (meeting-api, agent-api) consumes it as a library/seam.

**What a caller may not decide.** Every route but `/health` requires the caller credential
(`RUNTIME_API_TOKEN`, held by agent-api, meeting-api and the runtime). A spec's env never carries the
store backing or Pod scheduling (`VEXA_WORKSPACE_MOUNT_*`, `RUNTIME_K8S_*` are dropped); every mount in
its set must sit under the runtime's own store target (`VEXA_WORKSPACE_MOUNT_TARGET`), and a mount with
its own host source must name one of `RUNTIME_EXTRA_MOUNT_SOURCES`. The runtime signs every
`RuntimeEvent` callback (`X-Runtime-Signature`).

## Seams
| Direction | Neighbour | Via | What crosses |
|---|---|---|---|
| **consumes** | control plane (meeting-api · agent-api) | `runtime.v1` `WorkloadSpec` (create/get/list/stop/destroy) | profile + env + resources; a `WorkloadStatus` back |
| **spawns-over** | Docker / K8s / child process | Backend port (`docker` CLI · K8s · `ProcessBackend`) | the container/process for the profile |
| **produces** | each workload's `callbackUrl` | `runtime.v1` `RuntimeEvent` (durable callback queue) | every lifecycle transition (starting→…→destroyed) |
| **consumes** | scheduler callers | `schedule.v1` `ScheduleJob` (`Scheduler.schedule(spec)`) | a one-shot/cron HTTP-call request + retry/idempotency |
| **calls** | redis | sorted set `scheduler:jobs` (+ `scheduler:executing` / `:history` / `:idem:*`) | job JSON scored by `execute_at`; `tick()` pulls due |
| **calls** | the job's target service | the job's `request.url` (HTTP, injectable `dispatch`) | the scheduled HTTP request when due |

## Contracts
**Owns:** [`core/runtime/contracts/runtime.v1`](contracts/runtime.v1) (WorkloadSpec · WorkloadStatus
· RuntimeEvent + RuntimeState/StopReason enums) and
[`core/runtime/contracts/schedule.v1`](contracts/schedule.v1) (ScheduleJob · Request · Retry).
**Consumes:** none — it is the bottom of the stack; callers reference its `*.v1` by path.
Both are sealed in the registry [`contracts.seal.json`](../../contracts.seal.json). Schemas live next to each contract — not restated here.

## Isolated evaluation
`tests/` runs L1 contract (goldens ≡ schema), L2 unit (faked Backend/Store, `fakeredis` + `FakeClock`
so the scheduler advances deterministically), and L3 integration (`test_lifecycle.py` drives a real
process workload through the full `runtime.v1` state machine, validating every emitted event):
```bash
uv run pytest -q
```

## Status
- ✅ delivered — `runtime.v1` lifecycle over process / Docker / K8s backends, with quotas (O-RT-2)
- ✅ delivered — **workspace tenant isolation, enforced by the substrate on all three backends**
  (`mounts.py` + `isolation.py`): docker = one volume-subpath bind per granted mount (engine ≥ v26 for
  named-volume stores; `:ro` roles enforced); k8s = per-mount `subPath`+`readOnly` volumeMounts; process
  (lite) = per-subject uid + per-shared-workspace gids, 0700 tiers, default-deny sweep. A worker's
  filesystem contains ONLY its dispatch's mounts; no opt-out.
- ✅ delivered — **no child of a root process backend is root**: a workspace dispatch runs as its
  subject's uid (a canonical number below 100000 arithmetically, any other plain name from a
  root-owned registry), any other workload (a meeting bot) as a uid of its own with only its
  profile's `process_groups`, every child with a fresh private HOME and `no_new_privs`, and every
  child but a meeting bot under a seccomp filter refusing it a user namespace (`userns.py`). A child that
  cannot be isolated is refused, never started as root. Root's filesystem work never follows a link
  (`O_NOFOLLOW` opens on directory fds, an fd walk for re-owning a tree, hard-linked files left alone).
- ✅ delivered — group-scoped teardown on the process backend: each workload leads its own process
  group (`start_new_session=True`), and every ending path (observed self-exit, kill, cleanup, stop)
  signals the whole group — a self-exiting or stopped bot never strands its child tree. Declared
  limitation: descendants that detach into their own process group are out of the group signal's reach.
- ✅ delivered — durable `RuntimeEvent` callback delivery (enqueue + retry-until-ack)
- ✅ delivered — store port (InMemory / Redis) so workloads survive a process restart
- ✅ delivered — `schedule.v1` Scheduler: `scheduler:jobs` sorted set, `tick()` every 5s, HTTP dispatch, exponential-backoff retry, cron re-arm, idempotency, orphan recovery
- ⬜ planned — the scheduler fires scheduled-meeting jobs (a job whose request POSTs agent-api `/api/meeting/bot`)
