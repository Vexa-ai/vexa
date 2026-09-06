"""The worker: claim ONE due reaction by lease, run ONE step, advance. The entire runtime
behavior of the engine lives on this screen."""
from __future__ import annotations

from typing import Optional

from . import receipts
from .clock import Clock
from .db import DB, dumps, loads
from .model import Block, Done, NotPresent, Reaction, StepCtx, StepError, Wait
from .registry import Registry

LEASE_S = 90.0
BACKOFF_S = (5.0, 30.0, 120.0, 600.0)
MAX_ATTEMPTS = 6

# Was the engine parked by the instance gate on the previous tick? Module-level on purpose: what
# an operator needs in the log is the TRANSITION, not the state. `tick` runs about once a second,
# so a line per tick is a line nobody reads, while a line per change is the timeline of the outage.
_GATE_UP = False


def _log_gate(db: DB, up: bool) -> None:
    """Print the gate's edges, once each. The count is read only when parking BEGINS — it is the
    number an operator actually asks for ("how much is queued behind this?") and it must not cost
    a query per tick to answer."""
    global _GATE_UP
    if up == _GATE_UP:
        return
    _GATE_UP = up
    if up:
        rows = db.execute(
            "SELECT COUNT(*) FROM reaction WHERE status IN ('admitted','retrying')")
        n = rows[0][0] if rows else 0
        print(f"[loop] PARKED by the instance gate — {n} reaction(s) keep their place in the "
              f"queue; nothing is claimed, nothing is failed, no attempt is burned", flush=True)
    else:
        print("[loop] instance gate open — resuming", flush=True)


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


def tick(db: DB, registry: Registry, clock: Clock, *, emit=None, gate=None, present=None) -> bool:
    """One unit of work. Returns False when nothing was due (caller sleeps poll_ms).
    ``emit`` (optional) lets steps publish facts: (event_type, source_id, refs) -> int.

    ``gate`` (optional) is a ZERO-ARG PREDICATE answering "may work run at all?". False parks the
    entire engine — PARK, never drop.

    It is asked BEFORE `claim`, and that position is the whole design. Nothing is leased while the
    gate is up, so there is no lease to expire, no half-run step, and no row for the reconciler to
    reclaim; a fact keeps its `admitted`/`retrying` status, its `next_run_at`, its `attempt` and
    its `reason` exactly as it had them. When the gate opens, everything that arrived during the
    outage runs in the order it arrived, having lost nothing.

    Deliberately NOT done here: pushing `next_run_at` out on the parked rows. It reads like the
    tidier expression of "come back later", and it silently REORDERS the queue — a fact admitted
    one second before the gate opens is due immediately, while a fact admitted an hour earlier was
    pushed a minute into the future and now runs second. Leaving the rows untouched is both
    cheaper and the only version that preserves arrival order. Same reasoning for `attempt`: a
    parked reaction that retried itself into `failed` would be a dropped fact, just slower.

    ``present`` (optional) is a PREDICATE ON A DOMAIN NAME — "is this domain deployed?" — asked of
    every domain a step declared through `@reg.step(needs=…)` BEFORE the body runs. Asked before
    the body, so the absent door is never knocked on — a step that reached it and caught the
    connection error would be reporting an outage, which is a different fact with a different
    remedy.

    WHAT THE ABSENCE DOES IS THE STEP'S OWN DECLARATION (`@reg.step(absent=…)`, F-D20), because
    one rule cannot be right for every step: `abort` (the default) answers `NotPresent` and ends
    the reaction, which is correct for an agent turn and catastrophic for the eight steps standing
    behind an acknowledgement mail; `skip` records the skip and advances; `degrade` runs the body
    and tells it. PRD decision 40.7 asks for a product that keeps working with a domain removed,
    and only the first of the three was ever implemented.

    ``gate`` and ``present`` both default to None — no gate, everything present — so every existing
    caller (the fixtures, the storm, the offline suite) is unchanged, and `flows/` still imports
    nothing from `flows_integrations/` or `flows_steps/`: the engine core does not learn what an
    instance is or which domains a deployment has, the worker injects both.
    """
    if gate is not None:
        if not gate():
            _log_gate(db, True)
            return False       # "nothing to do, sleep" — the caller's idle path is already right
        _log_gate(db, False)
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
    absent = sorted(d for d in registry.needs(r.step) if present is not None and not present(d))
    # WHAT THE ABSENCE DOES IS THE STEP'S OWN DECLARATION (F-D20). It used to be one rule for
    # every step — terminal — and that rule silently deleted the rest of the no-agents product:
    # `invite_intake@3` ended at its third step of nine, so no bot was ever dispatched and no
    # meeting was ever recorded on a deployment with no agent domain, while the reaction read
    # `done` and nothing was paged. Absence DEGRADES what depends on the absent domain; it does
    # not abort what does not. See `Registry.step`'s three policies.
    if absent:
        # BEFORE the effect key is reserved and before the body is entered: there is no effect to
        # record an attempt at, and the step must not get the chance to reach the missing door.
        aborting = [d for d in absent if registry.absent_policy(r.step, d) == "abort"]
        if aborting:
            out = NotPresent(aborting[0],
                             detail=f"this deployment does not run {'/'.join(aborting)}")
            _not_present(db, r, clock, effect_key(r), out)
            return True
        skipping = [d for d in absent if registry.absent_policy(r.step, d) == "skip"]
        if skipping:
            _skipped(db, r, flow, clock, effect_key(r), skipping)
            return True
        # everything left is `degrade`: the body runs, and is told through `ctx.absent` below.
    key = effect_key(r)

    prior_receipt = receipts.get(db, key)
    if prior_receipt and prior_receipt.state == "confirmed":
        # crash landed between confirm and advance — never redo a confirmed effect
        _advance(db, r, flow, clock)
        return True

    receipts.reserve(db, key, r.reaction_id, r.step, clock)          # commit point A
    ctx = StepCtx(reaction=r, effect_key=key,
                  prior=receipts.prior(db, r.reaction_id), clock_now=clock.now(),
                  scratch=getattr(r, "_scratch", {}) or {}, emit=emit,
                  absent=frozenset(absent))
    ctx.flow = flow                       # the governing version's definition incl. params
    def _save_scratch() -> None:
        db.execute("UPDATE reaction SET scratch = :s WHERE reaction_id = :rid",
                   {"s": dumps(ctx.scratch), "rid": r.reaction_id})

    def _checkpoint() -> None:
        """MID-STEP: persist what has been done and push the lease out again.

        A step is normally shorter than `LEASE_S` and needs none of this. The attendee fan-out is
        not: it mints a transcript share, mints a scaffold and sends an SMTP message PER PERSON,
        so a 20-person room at ~4 s each runs past 90 s. What happened then was silent and
        expensive — `reclaim` returned the still-running reaction to the queue, a second worker
        claimed it, and because scratch is only written when the step RETURNS that worker began
        from an empty `sent` list: everybody already mailed was mailed a second time, with a
        second share token each.

        Both halves matter and they fail differently. Renewing the lease is what stops the second
        worker existing; saving scratch is what makes the second worker harmless if it does (a
        crash, a redeploy, a lease that expired anyway). One call buys both, so a step cannot take
        the cheap half by accident."""
        _save_scratch()
        db.execute(
            "UPDATE reaction SET lease_until = :lease, updated_at = :now WHERE reaction_id = :rid",
            {"lease": clock.now() + LEASE_S, "now": clock.now(), "rid": r.reaction_id})

    ctx.checkpoint = _checkpoint
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
    elif isinstance(out, NotPresent):
        _not_present(db, r, clock, key, out)
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


def _not_present(db: DB, r: Reaction, clock: Clock, key: str, out: NotPresent) -> None:
    """Terminal, with the reason on the reaction (PRD decision 40.7).

    `done`, not `failed`: nothing is broken and nobody should be paged. The REASON is what carries
    the difference — `projection.status`, `flows_timeline.list_reactions` and everything reading
    them already select it, so the degradation shows up wherever a person looks without a new
    column or a new surface. The remaining steps are not run: every one of them after an absent
    agent turn is about the result of that turn.

    A confirmed receipt is written for the same reason every other terminal outcome writes one —
    a redelivery of the same fact must not re-run this and must not answer differently.

    THIS IS NOW THE `abort` POLICY, not the only policy (F-D20). "The remaining steps are not run"
    is true of a step that has nothing to degrade to — an agent turn, a bot dispatch — and was
    false of everything else: see `_skipped` for the branch that keeps the flow moving."""
    receipts.reserve(db, key, r.reaction_id, r.step, clock)
    receipts.confirm(db, key, out.receipt(), None, clock)
    db.execute(
        """UPDATE reaction SET status = 'done', reason = :why, attempt = 0,
                  lease_until = NULL, updated_at = :now WHERE reaction_id = :rid""",
        {"why": out.reason, "now": clock.now(), "rid": r.reaction_id})


def _skipped(db: DB, r: Reaction, flow, clock: Clock, key: str, domains: list) -> None:
    """DEGRADE AND CONTINUE — the step is passed over, the reaction is not (F-D20).

    Two surfaces are written, because they answer two different questions and neither substitutes
    for the other:

    * the step's own RECEIPT, confirmed, carrying `skipped: "<domain>:not_present"`. That is what
      `flows_timeline` reads, so the skip shows up wherever a person looks — and, exactly as for
      every other terminal outcome, a redelivery of the same fact must not re-run this step and
      must not answer differently.
    * the reaction's SCRATCH, under `skipped[<step>]`. That is what a LATER STEP IN THE SAME
      REACTION reads: `email_minutes` has to know that `process_meeting` never wrote a report, and
      an absent key in `ctx.prior` cannot tell it whether the step was skipped or simply has not
      run yet.

    The reaction's own `reason` is deliberately NOT set: `_advance` clears it on every hop, and a
    reason that survives one step but not the next is worse than no reason at all. The reason for
    this step lives on this step's receipt, where it stays true.
    """
    reason = ", ".join(f"{d}:not_present" for d in domains)
    receipts.reserve(db, key, r.reaction_id, r.step, clock)
    receipts.confirm(db, key, {"outcome": "skipped", "domain": domains[0], "skipped": reason,
                               "detail": f"this deployment does not run {'/'.join(domains)}"},
                     None, clock)
    scratch = dict(getattr(r, "_scratch", {}) or {})
    scratch["skipped"] = {**(scratch.get("skipped") or {}), r.step: reason}
    db.execute("UPDATE reaction SET scratch = :s WHERE reaction_id = :rid",
               {"s": dumps(scratch), "rid": r.reaction_id})
    _advance(db, r, flow, clock)


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
        # THE STEP'S OWN RECEIPT IS PART OF THE TERMINAL STATE, and nothing used to say so: only
        # the `reaction` row was marked, the receipt stayed `reserved`, and `flows_timeline`
        # renders `reserved` as `in_flight`. A permanently failed `email_minutes` therefore read
        # as a report still on its way — the exact claim that module exists to make impossible.
        receipts.fail(db, effect_key(r), why, clock)
        db.execute(
            """UPDATE reaction SET status = 'failed', reason = :why,
                      lease_until = NULL, updated_at = :now WHERE reaction_id = :rid""",
            {"why": why, "now": clock.now(), "rid": r.reaction_id})
