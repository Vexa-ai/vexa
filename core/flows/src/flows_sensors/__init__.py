"""flows-sensors — the outside world turned into typed events, with no model call (ADR-0050 decision 1).

THE INTERFACE every sensor kind implements (`SensorKind`), and the one pool that runs them all
(`pool`). A kind is small: what it is called, which event types it produces, how often it polls, and
one `tick(sensor, cursor) -> Tick` that reads what changed since `cursor` and returns typed `Change`s
plus the next cursor. Everything else — which sensors exist, when each is due, leases, the cursor
checkpoint, dedupe, emitting into flows admission, failure counting and health — is the pool's, so
every kind gets it the same way.

    class MySensor(SensorKind):
        kind = "mail"                              # sensor ids are "<kind>:<uid>" or "<kind>:instance"
        event_types = ("mail.reply", "invite.received")
        scope = "instance"                         # "user" (one per person who needs it) | "instance"
        interval_s = 60.0

        def tick(self, sensor: SensorRow, cursor: str | None) -> Tick:
            ...                                    # read changes since cursor; FILTER here (no model)
            return Tick(changes=[Change("mail.reply", dedupe_key=msg_id, refs={...})],
                        cursor=new_cursor)         # raise SensorFault(reason) on a failed read

* ID — `<kind>:<uid>` for a per-person sensor (`scope = "user"`), `<kind>:instance` for one the
  deployment runs once (`scope = "instance"`, e.g. a shared inbound mailbox).
* CURSOR — opaque text the kind returns; the pool stores it only when it moves (a no-change tick
  writes nothing) and hands it back on the next tick. `Tick(resync=True)` drops it.
* FILTER — the kind's own, inside `tick`, before anything is emitted: a sensor emits only the events
  somebody could want. Per-flow narrowing is the trigger's `filter` (flows.triggers).
* EMIT — each `Change` is admitted into flows as `event_type` with `refs`, deduped by
  `sensor:<id>:<dedupe_key>` on the reaction table's constraint, so a replay after a crash or a
  resync re-emits nothing.
* HEALTH — `status` active | degraded | stopped, `failures`, `reason`, `last_tick_at`,
  `last_change_at`, `mode` (poll | push), read by `pool.health()` for the routine strip and
  `sensors_status`.
"""
from .base import Change, SensorFault, SensorKind, SensorRow, Tick  # noqa: F401
