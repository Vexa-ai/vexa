"""The worker: claim ONE due reaction by lease, run ONE step, advance. The entire runtime
behavior of the engine lives on this screen."""
from __future__ import annotations

from typing import Optional

from . import receipts
from .clock import Clock
from .db import DB, dumps, loads
from .model import Block, Done, Reaction, StepCtx, StepError, Wait
from .registry import Registry

LEASE_S = 90.0
BACKOFF_S = (5.0, 30.0, 120.0, 600.0)
MAX_ATTEMPTS = 6


def _row_to_reaction(row: tuple) -> Reaction:
    (rid, sid, et, refs, flow, ver, step, status, attempt,
     nra, bdl, lease, reason) = row
    return Reaction(rid, sid, et, loads(refs), flow, ver, step, status, attempt, nra, bdl, lease, reason)


_COLS = ("reaction_id, source_event_id, event_type, subject_refs, flow, flow_version, "
         "step, status, attempt, next_run_at, blocked_deadline, lease_until, reason")


def claim(db: DB, clock: Clock, *, lease_s: float = LEASE_S) -> Optional[Reaction]:
    now = clock.now()
    lock = " FOR UPDATE SKIP LOCKED" if db.dialect == "postgres" else ""
    rows = db.execute(
        f"""UPDATE reaction
            SET status = 'running', attempt = attempt + 1,
                lease_until = :lease, updated_at = :now
            WHERE reaction_id = (
              SELECT reaction_id FROM reaction
              WHERE status IN ('admitted','retrying') AND next_run_at <= :now
              ORDER BY next_run_at LIMIT 1{lock})
              AND status IN ('admitted','retrying')
            RETURNING {_COLS}""",
        {"now": now, "lease": now + lease_s})
    return _row_to_reaction(rows[0]) if rows else None


def effect_key(r: Reaction, target: str = "") -> str:
    return f"{r.reaction_id}:{r.step}" + (f":{target}" if target else "")


def tick(db: DB, registry: Registry, clock: Clock) -> bool:
    """One unit of work. Returns False when nothing was due (caller sleeps poll_ms)."""
    r = claim(db, clock)
    if r is None:
        return False
    flow = registry.get(r.flow, r.flow_version)
    key = effect_key(r)

    prior_receipt = receipts.get(db, key)
    if prior_receipt and prior_receipt.state == "confirmed":
        # crash landed between confirm and advance — never redo a confirmed effect
        _advance(db, r, flow, clock)
        return True

    receipts.reserve(db, key, r.reaction_id, r.step, clock)          # commit point A
    ctx = StepCtx(reaction=r, effect_key=key,
                  prior=receipts.prior(db, r.reaction_id), clock_now=clock.now())
    try:
        out = registry.steps[r.step](ctx)
    except StepError as e:
        _retry_or_fail(db, r, clock, str(e), retryable=e.retryable)
        return True
    except Exception as e:  # noqa: BLE001 — an unexpected crash is retryable but visible
        _retry_or_fail(db, r, clock, f"unexpected: {e!r}", retryable=True)
        return True

    if isinstance(out, Done):
        receipts.confirm(db, key, out.result, out.provider_ref, clock)   # commit point B
        _advance(db, r, flow, clock)
    elif isinstance(out, Wait):
        due = out.until if out.until is not None else clock.now() + float(out.seconds or 0)
        db.execute(
            """UPDATE reaction SET status = 'retrying', attempt = attempt - 1,
                      next_run_at = :due, lease_until = NULL, updated_at = :now
               WHERE reaction_id = :rid""",
            {"due": due, "now": clock.now(), "rid": r.reaction_id})     # a Wait burns no attempt
    elif isinstance(out, Block):
        deadline = clock.now() + out.deadline_s if out.deadline_s else None
        db.execute(
            """UPDATE reaction SET status = 'blocked', reason = :why,
                      blocked_deadline = :dl, lease_until = NULL, updated_at = :now
               WHERE reaction_id = :rid""",
            {"why": out.reason, "dl": deadline, "now": clock.now(), "rid": r.reaction_id})
    else:  # pragma: no cover — the type system should prevent this
        _retry_or_fail(db, r, clock, f"step returned {type(out).__name__}", retryable=False)
    return True


def _advance(db: DB, r: Reaction, flow, clock: Clock) -> None:
    nxt = flow.next_step(r.step)
    if nxt is None:
        db.execute(
            """UPDATE reaction SET status = 'done', lease_until = NULL,
                      attempt = 0, updated_at = :now WHERE reaction_id = :rid""",
            {"now": clock.now(), "rid": r.reaction_id})
    else:
        db.execute(
            """UPDATE reaction SET step = :step, status = 'retrying', attempt = 0,
                      next_run_at = :now, lease_until = NULL, reason = NULL, updated_at = :now
               WHERE reaction_id = :rid""",
            {"step": nxt, "now": clock.now(), "rid": r.reaction_id})


def _retry_or_fail(db: DB, r: Reaction, clock: Clock, why: str, *, retryable: bool) -> None:
    if retryable and r.attempt < MAX_ATTEMPTS:
        backoff = BACKOFF_S[min(r.attempt - 1, len(BACKOFF_S) - 1)]
        db.execute(
            """UPDATE reaction SET status = 'retrying', next_run_at = :due,
                      reason = :why, lease_until = NULL, updated_at = :now
               WHERE reaction_id = :rid""",
            {"due": clock.now() + backoff, "why": why, "now": clock.now(), "rid": r.reaction_id})
    else:
        db.execute(
            """UPDATE reaction SET status = 'failed', reason = :why,
                      lease_until = NULL, updated_at = :now WHERE reaction_id = :rid""",
            {"why": why, "now": clock.now(), "rid": r.reaction_id})
