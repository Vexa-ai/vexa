"""scheduling — O-MTG-3: compile a `ScheduledBot{cron|at}` → a `schedule.v1` job.

Front door (P6): import from here, never a deep module path.

The seam: a user-facing scheduling intent (`ScheduledBot`) compiles into a `schedule.v1`
ScheduleJob whose `request` is the real `POST /bots` bot-spawn call. The compiler validates
the emitted job AT THE SEAM against the unsealed-in-dev `runtime/contracts/schedule.v1` schema
(jsonschema by path — the SSOT). A Clock-gated `Scheduler` fires due jobs via an injectable
`dispatch` (production does HTTP; the eval captures the request, so no bot spawns), re-arms
`cron` jobs after each run, and `cancel`s a job so it never fires.

The `Clock` port + `FakeClock` and the `Scheduler` patterns MIRROR the runtime kernel
(`runtime/src/runtime_kernel/{clock,scheduler}.py`) — mirrored, not imported, because the
runtime is outside meeting-api's import graph; the SSOT remains the `schedule.v1` contract.

* ``ScheduledBot`` / ``compile_scheduled_bot`` — the intent + the compiler.
* ``conforms`` — the schedule.v1 schema-by-path validator (raises on non-conformance).
* ``Clock`` / ``SystemClock`` / ``FakeClock`` — the time port.
* ``Scheduler`` — schedule / tick / cancel / get / list, Clock-gated, capturing-dispatch ready.
* ``DEFAULT_BOTS_URL`` — the meeting-api ``/bots`` endpoint the fire targets.
"""
from .clock import Clock, FakeClock, SystemClock
from .compiler import (
    DEFAULT_BOTS_URL,
    ScheduledBot,
    compile_scheduled_bot,
    conforms,
)
from .scheduler import Scheduler

__all__ = [
    "Clock",
    "FakeClock",
    "SystemClock",
    "DEFAULT_BOTS_URL",
    "ScheduledBot",
    "compile_scheduled_bot",
    "conforms",
    "Scheduler",
]
