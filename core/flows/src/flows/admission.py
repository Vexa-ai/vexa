"""A fact becomes exactly one reaction PER MATCHING FLOW — idempotently, by constraint."""
from __future__ import annotations

import uuid

from .clock import Clock
from .db import DB, dumps
from .registry import Registry


def admit(db: DB, registry: Registry, clock: Clock, *, source_event_id: str,
          event_type: str, subject_refs: dict) -> int:
    """Returns how many reactions were newly created (0 = duplicate or no matching flow)."""
    created = 0
    for flow in registry.match(event_type):
        # one reaction per (fact, flow): the flow name joins the dedup key so a second flow on the
        # same event admits independently while a redelivery of the same fact stays a no-op.
        key = f"{source_event_id}::{flow.name}"
        now = clock.now()
        rows = db.execute(
            """INSERT INTO reaction (reaction_id, source_event_id, event_type, subject_refs,
                                     flow, flow_version, step, status, attempt, next_run_at,
                                     created_at, updated_at)
               VALUES (:rid, :sid, :et, :refs, :flow, :ver, :step, 'admitted', 0, :now, :now, :now)
               ON CONFLICT (source_event_id) DO NOTHING
               RETURNING reaction_id""",
            {"rid": uuid.uuid4().hex, "sid": key, "et": event_type, "refs": dumps(subject_refs),
             "flow": flow.name, "ver": flow.version, "step": flow.steps[0], "now": now})
        created += len(rows)
    return created
