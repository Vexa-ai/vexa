"""Streams — events for one recipient, kept until acknowledged (the factory concept's `stream`).

An agent session (or the founder) learns of work by reading its stream instead of by timer, and an
item stays there until its recipient acknowledges it, so a restarted session reads what it missed.

* :func:`append` — the `to_stream` step's write: one item per (recipient, source event). A retry of
  the step, or a redelivery of the event, answers the item already there.
* :func:`unacked` — the recipient's open items, oldest first, after a given item.
* :func:`ack` — sets `acked_at`/`acked_by` on the recipient's own items and records an `ack` signal
  on the run that appended each one. Acknowledging twice changes nothing.

`item_id` sorts by time: zero-padded microseconds, then a hash of recipient and source event. Engine
core: stdlib only, the DB is `(sql, params) -> rows`.
"""
from __future__ import annotations

import hashlib
import re
import uuid

from .db import DB, dumps, loads

#: A recipient: a role (`ceo`) or a subject (a uid, an address, a `crm:<id>`). No spaces, no slashes.
RECIPIENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._@+-]{0,199}$")
ITEM = re.compile(r"^\d{17}-[0-9a-f]{12}$")
#: The largest refs one item keeps (JSON characters); a bigger event names its record instead.
MAX_REFS = 16_000
MAX_READ = 200
MAX_ACK = 500


class StreamError(ValueError):
    pass


def check_recipient(recipient: str) -> str:
    r = str(recipient or "").strip()
    if not RECIPIENT.match(r):
        raise StreamError(f"not a recipient: {r[:60]!r}")
    return r


def kept_refs(refs: dict) -> dict:
    """The refs an item keeps: the run's refs as admitted, without the engine's own `_` keys."""
    return {k: v for k, v in (refs or {}).items() if not str(k).startswith("_")}


def item_id(recipient: str, source_event_id: str, now: float) -> str:
    h = hashlib.sha256(f"{recipient}\0{source_event_id}".encode()).hexdigest()[:12]
    return f"{int(now * 1_000_000):017d}-{h}"


def append(db: DB, now: float, *, recipient: str, source_event_id: str, event_type: str,
           refs: dict, reaction_id: str = "") -> dict:
    """Append one item. Answers `{item_id, status}`: `appended`, or `duplicate` with the id the
    first append wrote."""
    recipient = check_recipient(recipient)
    body = dumps(kept_refs(refs))
    if len(body) > MAX_REFS:
        raise StreamError(f"refs are {len(body)} characters; an item keeps at most {MAX_REFS}")
    iid = item_id(recipient, source_event_id, now)
    rows = db.execute(
        "INSERT INTO stream_item (item_id, recipient, source_event_id, event_type, refs, reaction_id, "
        "created_at, acked_at, acked_by) VALUES (:id, :r, :src, :et, :refs, :rid, :now, NULL, NULL) "
        "ON CONFLICT (recipient, source_event_id) DO NOTHING RETURNING item_id",
        {"id": iid, "r": recipient, "src": source_event_id, "et": event_type, "refs": body,
         "rid": reaction_id or None, "now": now})
    if rows:
        return {"item_id": rows[0][0], "status": "appended"}
    have = db.execute("SELECT item_id FROM stream_item WHERE recipient = :r AND source_event_id = :src",
                      {"r": recipient, "src": source_event_id})
    return {"item_id": have[0][0] if have else None, "status": "duplicate"}


def unacked(db: DB, recipient: str, *, after: str = "", limit: int = 50) -> list[dict]:
    """The recipient's unacknowledged items after ``after`` (an item id), oldest first."""
    recipient = check_recipient(recipient)
    if after and not ITEM.match(after):
        raise StreamError(f"not an item id: {after[:60]!r}")
    rows = db.execute(
        "SELECT item_id, event_type, refs, created_at FROM stream_item WHERE recipient = :r "
        "AND acked_at IS NULL AND item_id > :after ORDER BY item_id LIMIT :n",
        {"r": recipient, "after": after or "", "n": max(1, min(int(limit or 50), MAX_READ))})
    return [{"item_id": iid, "event_type": et, "refs": loads(refs) if refs else {}, "created_at": at}
            for iid, et, refs, at in rows]


def ack(db: DB, now: float, recipient: str, ids: list, *, actor: str, reason: str = "") -> dict:
    """Acknowledge the recipient's own items. Answers which ids were `acked`, which were acked
    `already`, and which are `unknown` in this stream (another stream's id is unknown here)."""
    recipient = check_recipient(recipient)
    ids = [str(i) for i in ids or []]
    if len(ids) > MAX_ACK:
        raise StreamError(f"at most {MAX_ACK} ids per ack")
    out: dict = {"acked": [], "already": [], "unknown": []}
    for iid in dict.fromkeys(ids):
        rows = db.execute(
            "UPDATE stream_item SET acked_at = :now, acked_by = :by WHERE recipient = :r "
            "AND item_id = :id AND acked_at IS NULL RETURNING reaction_id",
            {"now": now, "by": actor, "r": recipient, "id": iid})
        if rows:
            out["acked"].append(iid)
            _receipt(db, now, rows[0][0], recipient, iid, actor, reason)
            continue
        seen = db.execute("SELECT 1 FROM stream_item WHERE recipient = :r AND item_id = :id",
                          {"r": recipient, "id": iid})
        out["already" if seen else "unknown"].append(iid)
    return out


def _receipt(db: DB, now: float, reaction_id, recipient: str, iid: str, actor: str, reason: str) -> None:
    """The `ack` signal on the run that appended the item: who acknowledged it, when, why. A run the
    retention purge already removed keeps its receipt on the item alone (`acked_at`, `acked_by`)."""
    if not reaction_id or not db.execute("SELECT 1 FROM reaction WHERE reaction_id = :r", {"r": reaction_id}):
        return
    db.execute(
        "INSERT INTO signal (signal_id, reaction_id, kind, actor, reason, created_at, consumed_at) "
        "VALUES (:sid, :rid, 'ack', :actor, :why, :now, :now)",
        {"sid": uuid.uuid4().hex, "rid": reaction_id, "actor": actor,
         "why": dumps({"recipient": recipient, "item_id": iid, "reason": str(reason or "")[:500]}),
         "now": now})
