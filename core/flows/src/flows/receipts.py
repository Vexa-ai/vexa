"""Effect receipts — what makes retries safe. Reserve BEFORE the effect, confirm after;
a resumer consults the receipt before ever touching the world again."""
from __future__ import annotations

from typing import Optional

from .clock import Clock
from .db import DB, dumps, loads
from .model import Receipt


def get(db: DB, key: str) -> Optional[Receipt]:
    rows = db.execute(
        "SELECT effect_key, reaction_id, step, state, provider_ref, result "
        "FROM effect_receipt WHERE effect_key = :k", {"k": key})
    if not rows:
        return None
    k, rid, step, state, pref, result = rows[0]
    return Receipt(k, rid, step, state, pref, loads(result))


def reserve(db: DB, key: str, reaction_id: str, step: str, clock: Clock) -> None:
    db.execute(
        """INSERT INTO effect_receipt (effect_key, reaction_id, step, state, attempted_at)
           VALUES (:k, :rid, :step, 'reserved', :now)
           ON CONFLICT (effect_key) DO NOTHING""",
        {"k": key, "rid": reaction_id, "step": step, "now": clock.now()})


def confirm(db: DB, key: str, result: dict, provider_ref: Optional[str], clock: Clock) -> None:
    db.execute(
        """UPDATE effect_receipt SET state = 'confirmed', result = :res,
                  provider_ref = :pref, confirmed_at = :now
           WHERE effect_key = :k""",
        {"k": key, "res": dumps(result), "pref": provider_ref, "now": clock.now()})


def prior(db: DB, reaction_id: str) -> dict[str, dict]:
    """Confirmed results of earlier steps, keyed by step name — a later step's inputs."""
    rows = db.execute(
        "SELECT step, result FROM effect_receipt "
        "WHERE reaction_id = :rid AND state = 'confirmed'", {"rid": reaction_id})
    return {step: loads(result) for step, result in rows}
