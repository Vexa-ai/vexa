# contracts/schedule.v1 — the runtime scheduler's job spec

The schema that governs `schedule(spec)` on the runtime kernel's `Scheduler`: an HTTP-call **request**
scheduled for future execution, either **one-shot** (`execute_at`) or **recurring** (`cron`), with a
retry/backoff policy and an idempotency key. Derived from 0.11 `runtime-api`'s real job shape
(`runtime_api/scheduler.py`).

- `schedule.schema.json` — the source of truth (JSON Schema 2020-12). `$defs`: `ScheduleJob`, `Request`, `Retry`.
- `validate.mjs` — gate:schema; each golden `<Shape>.<case>.json` validates against `#/$defs/<Shape>`.
- `golden/` — `ScheduleJob.one-shot.json` (fires once at `execute_at`) and `ScheduleJob.cron.json` (re-arms).

Served by the runtime at `POST /schedule`, `GET /schedule` and `DELETE /schedule/{job_id}`, each behind
the runtime caller credential (`Authorization: Bearer <RUNTIME_API_TOKEN>`, runtime.v1
`CallerCredential`); errors are runtime.v1 `Error`. The runtime treats a job's `request` as opaque: the
headers a target requires (agent-api's `X-Vexa-Dispatch-Signature` on a routine's `/invocations`) are
the scheduling caller's to put there.

Sealed in `contracts.seal.json` (gate:contract-version).
