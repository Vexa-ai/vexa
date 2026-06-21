# meeting_api.scheduling — recurring/scheduled bots (O-MTG-3)

Compiles a user-facing **`ScheduledBot{at|cron}`** intent into a **`schedule.v1` job** whose
captured request is the literal `POST /bots` bot-spawn call, then fires it under an injectable
clock.

- **`compiler.py`** — `ScheduledBot` (validated: exactly one of `at|cron`) → `compile_scheduled_bot`
  emits a `schedule.v1` ScheduleJob (`at`→`execute_at` one-shot, `cron`→`cron` recurring); the
  job's `request` is the `POST …/bots` call (method/url/headers/body = the parent `MeetingCreate`
  bot-spawn shape). `conforms()` validates the emitted job by path against
  `runtime/contracts/schedule.v1` — the contract is the SSOT at the seam.
- **`clock.py`** — `Clock` port + `SystemClock`/`FakeClock` (mirrored from the runtime's clock
  pattern; the runtime package is outside meeting-api's import graph, so the shape is duplicated,
  not imported — the shared SSOT is the schedule.v1 contract).
- **`scheduler.py`** — a `Clock`-gated `Scheduler` (`schedule`/`tick`/`cancel`/`get`/`list`):
  sorted-by-`execute_at`, idempotency-deduped, injectable `dispatch`; a cron job **re-arms** for its
  next occurrence after firing; `cancel` removes it.

**Eval:** `tests/test_scheduling.py` — compile→conform, a `FakeClock` fires the captured `POST /bots`
exactly once (no real bot spawns), cron re-arms, cancel removes. Autonomous (no clock wall-time, no
meeting, no bot). Rides `gate:python`; the umbrella `gate:eval` requires this harness to exist.
