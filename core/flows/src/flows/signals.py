"""Human/operator verbs. A signal is a ROW — auditable, replayable — never a mutation from a shell."""
from __future__ import annotations

import uuid

from .clock import Clock
from .db import DB


def _record(db: DB, reaction_id: str, kind: str, actor: str, reason: str | None, clock: Clock) -> None:
    db.execute(
        """INSERT INTO signal (signal_id, reaction_id, kind, actor, reason, created_at, consumed_at)
           VALUES (:sid, :rid, :kind, :actor, :why, :now, :now)""",
        {"sid": uuid.uuid4().hex, "rid": reaction_id, "kind": kind,
         "actor": actor, "why": reason, "now": clock.now()})


def resume(db: DB, reaction_id: str, actor: str, clock: Clock, reason: str | None = None) -> bool:
    """Approve / unblock: blocked → due now. For a blocked step the HUMAN IS THE EFFECT —
    the signal confirms the step's receipt (result: who resumed), so the loop's
    confirmed-receipt check advances past the gate instead of re-running it into the
    same block forever (bug caught by the fixture on day one)."""
    rows = db.execute(
        """UPDATE reaction SET status = 'retrying', next_run_at = :now,
                  blocked_deadline = NULL, updated_at = :now
           WHERE reaction_id = :rid AND status = 'blocked' RETURNING reaction_id, step""",
        {"now": clock.now(), "rid": reaction_id})
    if rows:
        _rid, step = rows[0]
        key = f"{reaction_id}:{step}"
        from . import receipts
        receipts.reserve(db, key, reaction_id, step, clock)
        receipts.confirm(db, key, {"resumed_by": actor, "reason": reason}, None, clock)
        _record(db, reaction_id, "resume", actor, reason, clock)
    return bool(rows)


def retry(db: DB, reaction_id: str, actor: str, clock: Clock, reason: str | None = None) -> bool:
    """Operator replay of a failed reaction: a NEW attempt, causally linked by the signal row.
    The ledger of what happened is never mutated — receipts stay."""
    rows = db.execute(
        """UPDATE reaction SET status = 'retrying', attempt = 0, next_run_at = :now,
                  reason = NULL, updated_at = :now
           WHERE reaction_id = :rid AND status = 'failed' RETURNING reaction_id""",
        {"now": clock.now(), "rid": reaction_id})
    if rows:
        _record(db, reaction_id, "retry", actor, reason, clock)
    return bool(rows)


def cancel(db: DB, reaction_id: str, actor: str, clock: Clock, reason: str | None = None) -> bool:
    rows = db.execute(
        """UPDATE reaction SET status = 'cancelled', reason = :why, updated_at = :now
           WHERE reaction_id = :rid AND status IN ('admitted','retrying','blocked','running')
           RETURNING reaction_id""",
        {"why": reason or "cancelled", "now": clock.now(), "rid": reaction_id})
    if rows:
        _record(db, reaction_id, "cancel", actor, reason, clock)
    return bool(rows)


def wake(db: DB, reaction_id: str, actor: str, clock: Clock, reason: str | None = None) -> bool:
    """Re-check NOW a reaction that is deliberately sleeping between polls.

    The gap the other three verbs leave: ``resume`` needs 'blocked', ``retry`` needs
    'failed', and a reaction waiting on a condition is neither -- it sits in 'retrying'
    with next_run_at far out. An operator who has just satisfied that condition would
    otherwise have to UPDATE the table by hand.

    Deliberately narrow: it moves the schedule and nothing else. The attempt counter, the
    receipts and the status all stay as they were, so a wake can never launder a failure
    into a fresh attempt -- that is what ``retry`` is for."""
    rows = db.execute(
        """UPDATE reaction SET next_run_at = :now, updated_at = :now
           WHERE reaction_id = :rid AND status IN ('retrying','admitted')
           RETURNING reaction_id""",
        {"now": clock.now(), "rid": reaction_id})
    if rows:
        _record(db, reaction_id, "wake", actor, reason, clock)
    return bool(rows)
