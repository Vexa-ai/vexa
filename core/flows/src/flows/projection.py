"""The status view — what happened, why, what is waiting now. SQL over the two tables;
the product/operator surface reads THIS, never runtime internals."""
from __future__ import annotations

from .db import DB, loads


def status(db: DB, reaction_id: str) -> dict:
    rows = db.execute(
        """SELECT reaction_id, event_type, flow, flow_version, step, status, attempt,
                  next_run_at, blocked_deadline, reason FROM reaction
           WHERE reaction_id = :rid""", {"rid": reaction_id})
    if not rows:
        return {}
    (rid, et, flow, ver, step, st, attempt, nra, bdl, reason) = rows[0]
    receipts = db.execute(
        """SELECT step, state, provider_ref, result FROM effect_receipt
           WHERE reaction_id = :rid ORDER BY attempted_at""", {"rid": rid})
    return {
        "reaction_id": rid, "event_type": et, "flow": f"{flow}@{ver}",
        "step": step, "status": st, "attempt": attempt,
        "next_run_at": nra, "blocked_deadline": bdl, "reason": reason,
        "receipts": [
            {"step": s, "state": state, "provider_ref": pref, "result": loads(res)}
            for s, state, pref, res in receipts],
    }


def waiting(db: DB) -> list[dict]:
    rows = db.execute(
        """SELECT reaction_id, flow, step, status, reason, next_run_at FROM reaction
           WHERE status IN ('blocked','retrying','failed') ORDER BY next_run_at""")
    return [{"reaction_id": r, "flow": f, "step": s, "status": st, "reason": why, "next_run_at": n}
            for r, f, s, st, why, n in rows]
