# lifecycle.v1 — meeting-lifecycle ↔ bot-orchestration (to formalize at MVP3)

Idempotent commands (`ensure-running`, `ensure-stopped`) + durable exit events.

The semantics already exist — paid for twice in production:
- runtime-api durable exit-callbacks (260421 Pack J),
- meeting-api container-stop outbox (`container_stop_outbox.py`, Pack D.2,
  issue #266: 3-of-20 DELETEs dropped under load, orphan pods 12+ min).

MVP3 lifts those hand-built reliability layers into the contract: commands are
idempotent, exit events are acknowledged-durable, retry/DLQ behavior is spec,
fixtures are recorded command/event streams.
