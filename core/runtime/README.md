# runtime — the kernel (isolated spawn + the scheduler) (Python)

## Purpose
The core **kernel**: it spawns and supervises isolated workloads through the `runtime.v1`
lifecycle over a pluggable Backend (process / Docker / K8s), and runs a redis-backed `Scheduler`
that holds `schedule.v1` HTTP-call jobs in a sorted set and HTTP-POSTs them when due. **Mechanism,
not policy (P11):** a `profile` is an opaque name — the kernel knows docker/k8s/process, not what a
"bot" or "agent" *is*. Python because this is the runtime/tooling ecosystem and the control plane
(meeting-api, agent-api) consumes it as a library/seam.

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
Both seal into the registry [`contracts.seal.json`](../../contracts.seal.json) (`schedule.v1`
unsealed until `pnpm seal:contracts`). Schemas live next to each contract — not restated here.

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
- ✅ delivered — durable `RuntimeEvent` callback delivery (enqueue + retry-until-ack)
- ✅ delivered — store port (InMemory / Redis) so workloads survive a process restart
- ✅ delivered — `schedule.v1` Scheduler: `scheduler:jobs` sorted set, `tick()` every 5s, HTTP dispatch, exponential-backoff retry, cron re-arm, idempotency, orphan recovery
- ⬜ planned — the scheduler fires scheduled-meeting jobs (a job whose request POSTs agent-api `/api/meeting/bot`)
