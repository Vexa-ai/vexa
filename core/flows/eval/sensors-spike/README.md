# `core/flows/eval/sensors-spike` — the ADR-0050 shape, measured

A throwaway proof for [ADR-0050](../../../../docs/adr/0050-flows-sensors-events-triggers.md): one shared
calendar sensor per connection feeds typed `calendar.event.*` events into a `relative` trigger whose
keyed schedule moves when the meeting moves and is cancelled when it is cancelled. It is not product
code and nothing imports it.

- `scenario` — correctness: create, reschedule, cancel, decline, no video link; exactly one fire.
- `load` — cost per sensor tick for N sensors on a shared timer heap and K threads.

The provider is a fake that implements the Calendar incremental-sync contract (`syncToken` →
changed items + `nextSyncToken`, 410 on a bad token). Needs Postgres and `pg8000`; run it on a
build host, never against a live stack:

    python spike.py scenario --dsn postgres://…  --interval 1 --lead 5
    python spike.py load --dsn postgres://… --sensors 10000 --interval 60 --seconds 120 --threads 16

Results are recorded in the ADR.
