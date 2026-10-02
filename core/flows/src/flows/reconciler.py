"""Two UPDATE statements on a timer: reclaim expired leases; escalate passed block deadlines.
This is the entire crash-recovery and escalation machinery."""
from __future__ import annotations

from .clock import Clock
from .db import DB


def reclaim(db: DB, clock: Clock) -> int:
    """A worker died mid-step: its lease expires, the reaction returns to the queue.
    The receipt it may have left decides on resume whether the effect already happened."""
    rows = db.execute(
        """UPDATE reaction SET status = 'retrying', next_run_at = :now,
                  lease_until = NULL, updated_at = :now
           WHERE status = 'running' AND lease_until IS NOT NULL AND lease_until < :now
           RETURNING reaction_id""",
        {"now": clock.now()})
    return len(rows)


def escalate(db: DB, clock: Clock) -> int:
    """A blocked reaction whose deadline passed is a typed failure, not a forgotten row."""
    rows = db.execute(
        """UPDATE reaction SET status = 'failed',
                  reason = COALESCE(reason,'') || ' [blocked_deadline passed]', updated_at = :now
           WHERE status = 'blocked' AND blocked_deadline IS NOT NULL AND blocked_deadline < :now
           RETURNING reaction_id""",
        {"now": clock.now()})
    return len(rows)
