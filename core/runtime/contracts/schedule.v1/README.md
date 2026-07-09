# contracts/schedule.v1 — the runtime scheduler's job spec

The schema that governs `schedule(spec)` on the runtime kernel's `Scheduler`: an HTTP-call **request**
scheduled for future execution, either **one-shot** (`execute_at`) or **recurring** (`cron`), with a
retry/backoff policy and an idempotency key. Derived from 0.11 `runtime-api`'s real job shape
(`runtime_api/scheduler.py`).

- `schedule.schema.json` — the source of truth (JSON Schema 2020-12). `$defs`: `ScheduleJob`, `Request`, `Retry`.
- `validate.mjs` — gate:schema; each golden `<Shape>.<case>.json` validates against `#/$defs/<Shape>`.
- `golden/` — `ScheduleJob.one-shot.json` (fires once at `execute_at`) and `ScheduleJob.cron.json` (re-arms).

Unsealed (in development) until frozen into `v0.12/contracts.seal.json` via `pnpm seal:contracts`.
