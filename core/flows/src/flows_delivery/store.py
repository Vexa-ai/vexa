"""The ``delivery`` table and the ``delivery_send`` ledger — flows' one write surface for nudges.

Explicit SQL through the engine's ``DB`` protocol (``(sql, params) -> rows``), the same as
``flows.keyed``: Postgres in production, the sqlite double in the offline suite.

STAGES (``delivery.stage``):

    armed     waiting for the item's ``next_check_at``
    notified  the in-app notice is out; waiting for the idle window
    digest    waiting for the person's next digest
    done      terminal; ``outcome`` says why (seen · acted · closed · muted · emailed · digested ·
              email_off · cancelled · failed · no_address)

A row is keyed (person, ref): arming the same item again MOVES it (``armed``) or, once its cycle has
ended, RE-ARMS it for a later check — never a second row, never a second cycle for the same check.
"""
from __future__ import annotations

import json
from typing import Optional

ACTIVE = ("armed", "notified", "digest")
_COLS = ("person, ref, kind, org, workspace, room, owner, names, link, stage, wake_at, notice_id, "
         "outcome, attempts, version, armed_at, notified_at, emailed_at, digested_at, updated_at")


def _row(t: tuple) -> dict:
    d = dict(zip([c.strip() for c in _COLS.split(",")], t))
    try:
        d["names"] = json.loads(d["names"]) if d.get("names") else {}
    except (TypeError, ValueError):
        d["names"] = {}
    return d


def get(db, person: str, ref: str) -> Optional[dict]:
    rows = db.execute(f"SELECT {_COLS} FROM delivery WHERE person = :p AND ref = :r",
                      {"p": person, "r": ref})
    return _row(rows[0]) if rows else None


def arm(db, now: float, *, person: str, ref: str, kind: str, at: float, org: str = "",
        workspace: str = "", room: str = "", owner: str = "", names: Optional[dict] = None,
        link: str = "") -> str:
    """Arm (or move) the item's nudge for ``person`` at ``at``. Returns ``armed``, ``moved``,
    ``rearmed`` or ``unchanged``."""
    names_s = json.dumps({k: str(v)[:200] for k, v in (names or {}).items() if v}, sort_keys=True)
    old = get(db, person, ref)
    if old is None:
        db.execute("INSERT INTO delivery (person, ref, kind, org, workspace, room, owner, names, link, "
                   "stage, wake_at, attempts, version, armed_at, updated_at) VALUES (:p, :r, :k, :o, "
                   ":w, :rm, :ow, :n, :l, 'armed', :at, 0, 1, :now, :now)",
                   {"p": person, "r": ref, "k": kind, "o": org, "w": workspace, "rm": room,
                    "ow": owner, "n": names_s, "l": link, "at": float(at), "now": now})
        return "armed"
    if old["stage"] == "armed":
        if abs(float(old["wake_at"]) - float(at)) < 1.0:
            return "unchanged"
        db.execute("UPDATE delivery SET wake_at = :at, version = version + 1, names = :n, "
                   "updated_at = :now WHERE person = :p AND ref = :r",
                   {"at": float(at), "n": names_s, "now": now, "p": person, "r": ref})
        return "moved"
    if old["stage"] == "done" and float(at) > float(old["updated_at"]):
        # A NEW CHECK, after the last cycle ended: a fresh cycle. A redelivered envelope carrying the
        # same old `next_check_at` is at <= the moment the cycle ended, and changes nothing.
        db.execute("UPDATE delivery SET stage = 'armed', wake_at = :at, outcome = NULL, notice_id = NULL, "
                   "attempts = 0, version = version + 1, kind = :k, names = :n, link = :l, "
                   "armed_at = :now, notified_at = NULL, emailed_at = NULL, digested_at = NULL, "
                   "updated_at = :now WHERE person = :p AND ref = :r",
                   {"at": float(at), "k": kind, "n": names_s, "l": link, "now": now, "p": person,
                    "r": ref})
        return "rearmed"
    return "unchanged"


def finish(db, now: float, person: str, ref: str, outcome: str) -> bool:
    """End one person's nudge for an item. True when one was in flight."""
    rows = db.execute("UPDATE delivery SET stage = 'done', outcome = :o, updated_at = :now "
                      "WHERE person = :p AND ref = :r AND stage IN ('armed','notified','digest') "
                      "RETURNING ref", {"o": outcome, "now": now, "p": person, "r": ref})
    return bool(rows)


def finish_ref(db, now: float, ref: str, outcome: str, *, except_person: str = "") -> int:
    """End every person's nudge for an item (the card closed), or every one but ``except_person``
    (the next actor changed). Returns how many were in flight."""
    rows = db.execute("UPDATE delivery SET stage = 'done', outcome = :o, updated_at = :now "
                      "WHERE ref = :r AND person <> :x AND stage IN ('armed','notified','digest') "
                      "RETURNING person", {"o": outcome, "now": now, "r": ref, "x": except_person})
    return len(rows or [])


def move(db, now: float, person: str, ref: str, *, stage: str, wake_at: float, **cols) -> None:
    sets = ["stage = :stage", "wake_at = :wake", "updated_at = :now"]
    params = {"stage": stage, "wake": float(wake_at), "now": now, "p": person, "r": ref}
    for k, v in cols.items():
        if k not in ("notice_id", "notified_at", "emailed_at", "digested_at", "attempts", "outcome"):
            raise ValueError(f"not a delivery column: {k}")
        sets.append(f"{k} = :{k}")
        params[k] = v
    db.execute(f"UPDATE delivery SET {', '.join(sets)} WHERE person = :p AND ref = :r", params)


def due(db, now: float, *, stages=("armed", "notified"), limit: int = 200) -> list[dict]:
    marks = ", ".join(f":s{i}" for i in range(len(stages)))
    params = {f"s{i}": s for i, s in enumerate(stages)}
    params.update({"now": now, "n": int(limit)})
    rows = db.execute(f"SELECT {_COLS} FROM delivery WHERE stage IN ({marks}) AND wake_at <= :now "
                      "ORDER BY wake_at LIMIT :n", params)
    return [_row(r) for r in rows]


def digest_people(db, now: float, *, limit: int = 100) -> list[str]:
    rows = db.execute("SELECT DISTINCT person FROM delivery WHERE stage = 'digest' AND wake_at <= :now "
                      "LIMIT :n", {"now": now, "n": int(limit)})
    return [r[0] for r in rows]


def digest_rows(db, now: float, person: str) -> list[dict]:
    rows = db.execute(f"SELECT {_COLS} FROM delivery WHERE person = :p AND stage = 'digest' "
                      "AND wake_at <= :now ORDER BY armed_at", {"p": person, "now": now})
    return [_row(r) for r in rows]


# ── the ledger: one row per delivery outside the Inbox (D32) ────────────────────────────────────

def sent_today(db, person: str, day: str) -> int:
    rows = db.execute("SELECT COUNT(*) FROM delivery_send WHERE person = :p AND day = :d "
                      "AND status IN ('sending','sent')", {"p": person, "d": day})
    return int(rows[0][0] or 0) if rows else 0


def reserve(db, now: float, *, send_id: str, person: str, day: str, kind: str, refs: list[str],
            channel: str = "email") -> bool:
    """Write the receipt BEFORE the send. False when this exact delivery was already attempted —
    the caller must not send again (at most once, across crashes and replicas)."""
    rows = db.execute("INSERT INTO delivery_send (id, person, day, channel, kind, refs, status, at) "
                      "VALUES (:id, :p, :d, :c, :k, :refs, 'sending', :now) "
                      "ON CONFLICT (id) DO NOTHING RETURNING id",
                      {"id": send_id, "p": person, "d": day, "c": channel, "k": kind,
                       "refs": json.dumps(refs), "now": now})
    return bool(rows)


def settle(db, send_id: str, status: str, *, handle: str = "", reason: str = "") -> None:
    db.execute("UPDATE delivery_send SET status = :s, handle = :h, reason = :why WHERE id = :id",
               {"s": status, "h": handle or None, "why": (reason or None) and reason[:300],
                "id": send_id})


def sends(db, person: str) -> list[dict]:
    rows = db.execute("SELECT id, day, channel, kind, refs, status, at FROM delivery_send "
                      "WHERE person = :p ORDER BY at", {"p": person})
    return [{"id": r[0], "day": r[1], "channel": r[2], "kind": r[3], "refs": json.loads(r[4]),
             "status": r[5], "at": float(r[6])} for r in rows]
