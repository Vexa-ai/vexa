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


def _row_to_reaction(row: tuple):
    (rid, sid, et, refs, flow, ver, step, status, attempt,
     nra, bdl, lease, reason, scratch) = row
    return (Reaction(rid, sid, et, loads(refs), flow, ver, step, status, attempt, nra, bdl, lease, reason),
            loads(scratch))


_COLS = ("reaction_id, source_event_id, event_type, subject_refs, flow, flow_version, "
         "step, status, attempt, next_run_at, blocked_deadline, lease_until, reason, scratch")


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
    if not rows:
        return None
    r, scratch = _row_to_reaction(rows[0])
    r._scratch = scratch  # type: ignore[attr-defined]
    return r


def effect_key(r: Reaction, target: str = "") -> str:
    return f"{r.reaction_id}:{r.step}" + (f":{target}" if target else "")


def tick(db: DB, registry: Registry, clock: Clock, *, emit=None) -> bool:
    """One unit of work. Returns False when nothing was due (caller sleeps poll_ms).
    ``emit`` (optional) lets steps publish facts: (event_type, source_id, refs) -> int."""
    r = claim(db, clock)
    if r is None:
        return False
    # Rows can OUTLIVE their code: a redeploy may retire a flow version or rename a step while
    # reactions reference them. That is a TYPED failure for the operator — never a KeyError that
    # kills the worker for everyone else (caught by the hostile suite).
    try:
        flow = registry.get(r.flow, r.flow_version)
    except KeyError:
        # Two DIFFERENT causes wear this one KeyError, and they need opposite handling.
        # BEHIND us: a redeploy retired the version — a real, typed, permanent failure.
        # AHEAD of us: the version was submitted through flows-api seconds ago and this
        # worker has not refreshed yet. Admission stamps the new version IMMEDIATELY, while
        # the worker only reloads every ~10s, so every reaction admitted inside that window
        # used to fail PERMANENTLY — the liquid layer racing its own admission. Refresh once
        # and look again; only a version still unknown afterwards is actually gone.
        try:
            registry.refresh_from_db(db)
            flow = registry.get(r.flow, r.flow_version)
        except Exception:  # noqa: BLE001
            _fail(db, r, clock,
                  f"unknown flow {r.flow}@{r.flow_version} — retired by deploy?")
            return True
    if r.step not in registry.steps or r.step not in flow.steps:
        _fail(db, r, clock, f"unknown step {r.step!r} in {r.flow}@{r.flow_version} — renamed by deploy?")
        return True
    key = effect_key(r)

    prior_receipt = receipts.get(db, key)
    if prior_receipt and prior_receipt.state == "confirmed":
        # crash landed between confirm and advance — never redo a confirmed effect
        _advance(db, r, flow, clock)
        return True

    receipts.reserve(db, key, r.reaction_id, r.step, clock)          # commit point A
    ctx = StepCtx(reaction=r, effect_key=key,
                  prior=receipts.prior(db, r.reaction_id), clock_now=clock.now(),
                  scratch=getattr(r, "_scratch", {}) or {}, emit=emit)
    ctx.flow = flow                       # the governing version's definition incl. params
    def _save_scratch() -> None:
        db.execute("UPDATE reaction SET scratch = :s WHERE reaction_id = :rid",
                   {"s": dumps(ctx.scratch), "rid": r.reaction_id})
    try:
        out = registry.steps[r.step](ctx)
        _save_scratch()
    except StepError as e:
        _save_scratch()
        _retry_or_fail(db, r, clock, str(e), retryable=e.retryable)
        return True
    except Exception as e:  # noqa: BLE001 — an unexpected crash is retryable but visible
        _save_scratch()
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


def _fail(db: DB, r: Reaction, clock: Clock, why: str) -> None:
    db.execute(
        """UPDATE reaction SET status = 'failed', reason = :why,
                  lease_until = NULL, updated_at = :now WHERE reaction_id = :rid""",
        {"why": why, "now": clock.now(), "rid": r.reaction_id})


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
