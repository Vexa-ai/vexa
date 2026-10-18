# flows_sensors — the outside world as typed events, no model call (ADR-0050)

A **sensor kind** (`base.SensorKind`) says what it is called (`kind`), which event types it emits
(`event_types`), its shortest check interval (`min_interval_s`) and after how many failures it reads
`degraded` (`degrade_after`), and implements one method:
`tick(sensor, cursor) -> Tick(changes=[Change(event_type, dedupe_key, refs)], cursor, resync)`.
It reads what changed since `cursor`, keeps nothing else, and raises `SensorFault(reason)` on a
failed read.

A **sensor** is a row a person owns (`sensor`, keyed by scope and name), made from chat:
`store.py` creates it as a draft, `store.test` dry-runs it (would emit / dropped with reasons, no
side effects) and records the tested version, `store.enable` switches on only a tested version, and
every change makes a new version that needs its own test. `pause` and `delete` end it.

`pool.py` runs every enabled sensor the same way: `run_due` claims due rows under a lease, ticks
them, admits each change through flows (deduped `sensor:<id>:<dedupe_key>`), checkpoints the cursor
only when it moves and backs off on failure (`degraded` after `degrade_after`, with the reason).
`dry_run` is the test; `health` reports status, last check, next check and last event for the
routine strip and `sensor_list`.

Kinds (`calendar.KINDS`): `calendar.py` (a person's Google Calendar through the credential broker's
`sensors` role, incremental by sync token) and `mailbox.py` (the deployment's shared mailbox, read
only: invitations, cancellations and replies to Vexa's own mail). Verify:
`uv run pytest tests/test_sensors_triggers.py tests/test_mailbox_sensor_kind.py`.
