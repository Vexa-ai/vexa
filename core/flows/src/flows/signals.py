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


# ── claim / release: someone outside the engine says "I am on this one" ──────────────────────────
#
# A reaction that waits on a person (`blocked`) or sleeps until its next check (`retrying`,
# `admitted`) can be picked up by someone working OUTSIDE the engine: an agent session taking the
# obligation the row stands for. Nothing in the ledger said so, so every projection of the queue
# showed such a row as waiting while the work on it was under way.
#
# `claim_hold` moves the row to `running` under a lease: the same two columns the worker's own
# claim writes (`status`, `lease_until`), so every reader that already counts `running` counts it.
# The holder, and the waiting state the row came from, are the claim's SIGNAL row: an audited fact,
# never a write into the step's scratch. `release_hold` puts the row back exactly where it was, and
# an expired lease does the same by itself on the reconciler's pass (`expire_holds`), so a holder
# that dies never leaves a row stuck in `running`, and never turns a sleeping row into a due one.
#
# The worker's own claim cannot collide with a hold: it takes only `admitted`/`retrying` rows, and
# a held row is `running`.

HOLDABLE = ("admitted", "retrying", "blocked")
HOLD_LEASE_S = 2 * 3600.0
HOLD_LEASE_MAX_S = 24 * 3600.0


def hold_of(db: DB, reaction_id: str) -> dict | None:
    """The live hold on a reaction, or None (never claimed, released, lapsed, or run by the
    engine's own worker). A hold is live while the row is `running` under the very lease its latest
    claim wrote: the lease, not the order of signal rows, says whose `running` it is, so two
    signals in the same instant can never make a released row look held or a held one free."""
    import json
    row = db.execute("SELECT status, lease_until FROM reaction WHERE reaction_id = :rid",
                     {"rid": reaction_id})
    if not row or row[0][0] != "running" or row[0][1] is None:
        return None
    lease = float(row[0][1])
    for (why, at) in db.execute(
            """SELECT reason, created_at FROM signal WHERE reaction_id = :rid AND kind = 'claim'
               ORDER BY created_at DESC LIMIT 50""", {"rid": reaction_id}):
        try:
            hold = json.loads(why or "{}")
        except ValueError:
            continue
        if abs(float(hold.get("lease_until") or 0) - lease) < 1e-6:
            hold["claimed_at"] = at
            return hold
    return None


def _restore(db: DB, reaction_id: str, hold: dict, clock: Clock, *, lapsed_only: bool) -> bool:
    prev = hold.get("from") or {}
    status = prev.get("status") if prev.get("status") in HOLDABLE else "retrying"
    lapsed = "AND lease_until IS NOT NULL AND lease_until < :now" if lapsed_only else ""
    rows = db.execute(
        f"""UPDATE reaction SET status = :st, next_run_at = :nra, blocked_deadline = :bdl,
                   lease_until = NULL, updated_at = :now
            WHERE reaction_id = :rid AND status = 'running' {lapsed}
            RETURNING reaction_id""",
        {"st": status, "nra": prev.get("next_run_at", clock.now()),
         "bdl": prev.get("blocked_deadline"), "now": clock.now(), "rid": reaction_id})
    if rows:
        _wake_waiters(db, reaction_id, clock)
    return bool(rows)


def claim_hold(db: DB, reaction_id: str, holder: str, clock: Clock, *,
               lease_s: float = HOLD_LEASE_S, reason: str | None = None) -> dict:
    """Take a waiting reaction: `running`, held by `holder` until now + `lease_s`.

    The same holder claiming again RENEWS the lease (a heartbeat) and keeps the state the row came
    from. Refused (``{"claimed": False, "refused": …}``) on a terminal row, on a row the engine's
    worker is running, and on a row another holder holds under a live lease. A lapsed hold goes
    back to its waiting state first, so the next claim takes it cleanly."""
    import json
    holder = (holder or "").strip()
    if not holder:
        return {"claimed": False, "refused": "no_holder"}
    lease_s = max(1.0, min(float(lease_s or HOLD_LEASE_S), HOLD_LEASE_MAX_S))
    now = clock.now()
    rows = db.execute("SELECT status, next_run_at, blocked_deadline, lease_until FROM reaction "
                      "WHERE reaction_id = :rid", {"rid": reaction_id})
    if not rows:
        return {"claimed": False, "refused": "missing"}
    status, nra, bdl, lease = rows[0]
    hold = hold_of(db, reaction_id)
    if hold is not None and lease is not None and lease < now:
        if _restore(db, reaction_id, hold, clock, lapsed_only=True):
            _record(db, reaction_id, "release", "lease", "lease expired", clock)
        return claim_hold(db, reaction_id, holder, clock, lease_s=lease_s, reason=reason)
    if status == "running":
        if hold is None:
            return {"claimed": False, "refused": "running"}
        if hold.get("holder") != holder:
            return {"claimed": False, "refused": "held", "holder": hold.get("holder"),
                    "lease_until": lease}
        rows = db.execute(
            """UPDATE reaction SET lease_until = :lease, updated_at = :now
               WHERE reaction_id = :rid AND status = 'running' RETURNING reaction_id""",
            {"lease": now + lease_s, "now": now, "rid": reaction_id})
        if not rows:
            return {"claimed": False, "refused": "moved"}
        _record(db, reaction_id, "claim", holder,
                json.dumps({"holder": holder, "from": hold.get("from"), "renewed": True,
                            "lease_until": now + lease_s, "note": reason}), clock)
        return {"claimed": True, "holder": holder, "lease_until": now + lease_s,
                "from": (hold.get("from") or {}).get("status"), "renewed": True}
    if status not in HOLDABLE:
        return {"claimed": False, "refused": "terminal", "status": status}
    rows = db.execute(
        """UPDATE reaction SET status = 'running', lease_until = :lease, updated_at = :now
           WHERE reaction_id = :rid AND status = :st RETURNING reaction_id""",
        {"lease": now + lease_s, "now": now, "rid": reaction_id, "st": status})
    if not rows:   # it moved between the read and the write
        return {"claimed": False, "refused": "moved"}
    _record(db, reaction_id, "claim", holder,
            json.dumps({"holder": holder, "note": reason, "lease_until": now + lease_s,
                        "from": {"status": status, "next_run_at": nra,
                                 "blocked_deadline": bdl}}), clock)
    return {"claimed": True, "holder": holder, "lease_until": now + lease_s, "from": status}


def release_hold(db: DB, reaction_id: str, actor: str, clock: Clock, *,
                 holder: str | None = None, reason: str | None = None) -> dict:
    """Give a held reaction back: it returns to the waiting state it was claimed from (status, next
    check, block deadline), and the release row carries the receipt (`reason`). Refused when the
    row is not under a hold, or when `holder` is named and is not the one holding it."""
    hold = hold_of(db, reaction_id)
    if hold is None:
        return {"released": False, "refused": "not_held"}
    if holder and hold.get("holder") != holder:
        return {"released": False, "refused": "held", "holder": hold.get("holder")}
    if not _restore(db, reaction_id, hold, clock, lapsed_only=False):
        return {"released": False, "refused": "not_held"}
    _record(db, reaction_id, "release", actor, reason, clock)
    return {"released": True, "to": (hold.get("from") or {}).get("status")}


def expire_holds(db: DB, clock: Clock) -> int:
    """The reconciler's pass for holds: a hold whose lease ran out returns its row to the waiting
    state it was claimed from, not to the queue as due (which is what a dead WORKER's lease means,
    `reconciler.reclaim`). It runs before `reclaim`, so a lapsed hold is never taken for a crash."""
    rows = db.execute("SELECT reaction_id FROM reaction WHERE status = 'running' "
                      "AND lease_until IS NOT NULL AND lease_until < :now", {"now": clock.now()})
    n = 0
    for (rid,) in rows:
        hold = hold_of(db, rid)
        if hold is not None and _restore(db, rid, hold, clock, lapsed_only=True):
            _record(db, rid, "release", "lease", "lease expired", clock)
            n += 1
    return n


# ── a hold is never undone by the engine's own transitions ───────────────────────────────────────
#
# A flow that supersedes an older reaction of the same subject (a newer fact replaces it: the
# factory's obligation flow moves a card's check this way) used `cancel`, the PERSON's verb. On a
# held row that ended the hold: the obligation was cancelled under its holder, the newer reaction
# went on to `blocked`, and the holder's `release` answered «not held» (1 Nov 2026, card 58bb9b68,
# held by a review session, woken by a GitHub sensor fact). A hold means «a person is on this»:
# while its lease is live the engine leaves the row in `running`, and its own transition applies
# after the release, or after the lease lapses.
#
# `supersede` is that transition: it ends only a WAITING row. A row that is `running` (held, or in
# the worker's hands this instant) is left alone and the answer says until when, so the newer
# reaction waits behind it with `wait_for_hold`. Releasing the hold (or the lease lapsing) wakes
# every reaction waiting on it, which then runs again and supersedes the restored row: the change
# is re-evaluated on release, not lost and not applied early. The person's `cancel` (a Drop) still
# ends a held row.

WAITS_ON_HOLD = "_waits_on_hold"     # the scratch key a waiting reaction names the held row by
HOLD_POLL_S = 5.0                    # a lapsed lease not yet restored: look again this soon


def supersede(db: DB, reaction_id: str, actor: str, clock: Clock,
              reason: str | None = None) -> dict:
    """A flow's own cancel of an older reaction. Ends it only while it waits (`admitted`,
    `retrying`, `blocked`); a `running` row is never touched. Returns
    ``{"superseded": True}``, or ``{"superseded": False, "running": True, "until": t,
    "holder": h | None}`` for a row someone holds (or the worker runs) until `t`, or
    ``{"superseded": False}`` for a row already closed or missing."""
    rows = db.execute(
        """UPDATE reaction SET status = 'cancelled', reason = :why, updated_at = :now
           WHERE reaction_id = :rid AND status IN ('admitted','retrying','blocked')
           RETURNING reaction_id""",
        {"why": reason or "superseded", "now": clock.now(), "rid": reaction_id})
    if rows:
        _record(db, reaction_id, "cancel", actor, reason, clock)
        return {"superseded": True}
    row = db.execute("SELECT status, lease_until FROM reaction WHERE reaction_id = :rid",
                     {"rid": reaction_id})
    if not row or row[0][0] != "running":
        return {"superseded": False}
    hold = hold_of(db, reaction_id)
    now = clock.now()
    lease = float(row[0][1]) if row[0][1] is not None else now
    return {"superseded": False, "running": True,
            "until": lease if lease > now else now + HOLD_POLL_S,
            "holder": (hold or {}).get("holder")}


def wait_for_hold(ctx, reaction_id: str, until: float):
    """The step's answer when `supersede` found the row running: sleep until `until` and name the
    row in scratch, so its release (or lapse) wakes this reaction at once. The step pops the key
    when it runs again and the row is free."""
    from .model import Wait
    ctx.scratch[WAITS_ON_HOLD] = reaction_id
    return Wait(until=until)


def _wake_waiters(db: DB, reaction_id: str, clock: Clock) -> int:
    """Wake every reaction that waits on `reaction_id`'s hold (`wait_for_hold`)."""
    import json
    n = 0
    for (rid, scratch) in db.execute(
            "SELECT reaction_id, scratch FROM reaction WHERE status IN ('admitted','retrying') "
            "AND scratch LIKE :pat", {"pat": f"%{reaction_id}%"}):
        try:
            waits = json.loads(scratch or "{}").get(WAITS_ON_HOLD)
        except ValueError:
            continue
        if waits == reaction_id and wake(db, rid, f"release:{reaction_id}", clock,
                                         reason="the hold it waited on ended"):
            n += 1
    return n
