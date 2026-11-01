"""Planned meetings reach Flows durably: the ``meeting.planned.changed`` outbox (ADR-0052 §4).

Flows owns the clock (ADR-0046) and starts a booked meeting's bot at ``pod_at`` through the start
route (``bot_spawn/start_router.py``). For that it must hear about EVERY planned row that is created,
moved, cancelled, deleted, or has its ``auto_join`` toggled — the API's ``POST/PATCH/DELETE
/meetings``, calendar sync, the terminal, a claim that turns the row into a bot. ``events.publish``
cannot carry that: it is best-effort and swallows failures, so a Flows outage would lose a booking.

So the fact is written into a table, ``meeting_planned_outbox``, IN THE SAME TRANSACTION as the row
change, and a drain delivers it afterwards — the CRM's outbox pattern (``core/crm/src/crm/outbox.py``):

* **Capture, one place for every writer.** A SQLAlchemy ``after_flush`` hook on the ``Meeting`` model
  (``install``, run when ``sessions.models`` is imported) sees every ORM insert, update and delete of
  a meeting row in the flush that writes it, and inserts the fact through the same connection — so the
  fact commits or rolls back with the row. A write that does not change the fact (a stamp on a
  scheduled row, a status that never was ``scheduled``) writes nothing.
* **The fact** carries ``meeting_id``, ``uid`` (the owner), ``status`` (``deleted`` for a deleted row),
  ``auto_join`` (false when off or stopped), ``pod_at`` / ``join_at`` / ``deadline`` (from the start and
  ``BOOKED_POD_LEAD_S`` / ``BOOKED_JOIN_LEAD_S`` / ``BOOKED_GRACE_S``, owned by meeting-api config), and
  ``revision`` — the outbox row id, monotonic, so per meeting a later fact always has a larger one. It
  carries no URL and no passcode.
* **Delivery** (``deliver``) is at least once and in revision order: pending rows in id order, each
  posted to Flows ``POST /events`` with this service's flows key (the edge ``events.publish`` uses);
  the first one Flows does not accept stops the pass and stays pending, with every row after it, and
  is retried on the next pass. ``source_event_id`` is ``planned-<meeting_id>-r<revision>``, so a row
  delivered twice (a crash between Flows' 202 and the commit) is one fact to Flows. Run single-flight
  across replicas (``__main__``'s guarded loop); no session is held while posting.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

EVENT = "meeting.planned.changed"
TABLE = "meeting_planned_outbox"
#: ADR-0052 §2. Read at write time; the fact carries the computed times, Flows reads no setting.
DEFAULT_BOOKED_POD_LEAD_S = 600
DEFAULT_BOOKED_JOIN_LEAD_S = 120
DEFAULT_BOOKED_GRACE_S = 600
DELIVER_LIMIT = 100
TIMEOUT_S = 5.0


def _seconds(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(float(raw))
    except ValueError:
        return default
    return value if value >= 0 else default


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def planned_fact(*, meeting_id: int, user_id: int, status: Optional[str],
                 data: Optional[dict]) -> dict:
    """The ``meeting.planned.changed`` refs for one row state (``revision`` is added at delivery)."""
    data = data if isinstance(data, dict) else {}
    start = _parse_iso(data.get("scheduled_at")) if status == "scheduled" else None
    on = status == "scheduled" and data.get("auto_join") is not False \
        and not data.get("stop_requested") and start is not None
    return {
        "meeting_id": meeting_id,
        "uid": str(user_id),
        "status": status or "deleted",
        "auto_join": bool(on),
        "pod_at": _iso(start - timedelta(seconds=_seconds("BOOKED_POD_LEAD_S",
                                                          DEFAULT_BOOKED_POD_LEAD_S)))
        if start else None,
        "join_at": _iso(start - timedelta(seconds=_seconds("BOOKED_JOIN_LEAD_S",
                                                           DEFAULT_BOOKED_JOIN_LEAD_S)))
        if start else None,
        "deadline": _iso(start + timedelta(seconds=_seconds("BOOKED_GRACE_S",
                                                            DEFAULT_BOOKED_GRACE_S)))
        if start else None,
    }


# ── capture: one hook, every ORM writer ─────────────────────────────────────────────────────────

def _facts_of_flush(session) -> list[dict]:
    """The facts this flush owes Flows, from the pre-flush state of every Meeting it writes."""
    from sqlalchemy import inspect
    from sqlalchemy.orm.attributes import get_history

    from .sessions.models import Meeting

    out: list[dict] = []
    for obj in list(session.new):
        if isinstance(obj, Meeting) and obj.status == "scheduled":
            out.append(planned_fact(meeting_id=obj.id, user_id=obj.user_id, status=obj.status,
                                    data=obj.data))
    for obj in list(session.dirty):
        if not isinstance(obj, Meeting) or not session.is_modified(obj):
            continue
        status_h = get_history(obj, "status")
        prev_status = status_h.deleted[0] if status_h.deleted else obj.status
        if "scheduled" not in (prev_status, obj.status):
            continue
        new = planned_fact(meeting_id=obj.id, user_id=obj.user_id, status=obj.status, data=obj.data)
        # Compared with the LAST fact written for this meeting, not with attribute history: a JSONB
        # changed in place (`flag_modified`) has no reliable "before". Same fact → nothing owed.
        if _last_fact(session, obj.id) == new:
            continue
        out.append(new)
    for obj in list(session.deleted):
        if not isinstance(obj, Meeting):
            continue
        committed = inspect(obj).committed_state.get("status", obj.status)
        if committed == "scheduled" or obj.status == "scheduled":
            out.append(planned_fact(meeting_id=obj.id, user_id=obj.user_id, status=None, data={}))
    return out


def _last_fact(session, meeting_id: int) -> Optional[dict]:
    from sqlalchemy import select

    from .sessions.models import MeetingPlannedOutbox as O

    return session.connection().execute(
        select(O.event).where(O.meeting_id == meeting_id).order_by(O.id.desc()).limit(1)
    ).scalar()


def _after_flush(session, _flush_context) -> None:
    facts = _facts_of_flush(session)
    if not facts:
        return
    from .sessions.models import MeetingPlannedOutbox

    session.connection().execute(
        MeetingPlannedOutbox.__table__.insert(),
        [{"meeting_id": f["meeting_id"], "event": f} for f in facts],
    )


_installed = False


def install() -> None:
    """Hook the capture onto every ORM session (idempotent)."""
    global _installed
    if _installed:
        return
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    event.listen(Session, "after_flush", _after_flush)
    _installed = True


# ── delivery: at least once, in revision order ──────────────────────────────────────────────────

def _flows() -> tuple[str, str]:
    return ((os.getenv("VEXA_FLOWS_API_URL") or "").rstrip("/"),
            (os.getenv("VEXA_FLOWS_API_KEY") or "").strip())


def body_of(row_id: int, event: dict) -> dict:
    """The ``POST /events`` body for one outbox row (its id is the fact's revision)."""
    refs = {**event, "revision": int(row_id)}
    return {"event_type": EVENT,
            "source_event_id": f"planned-{event['meeting_id']}-r{int(row_id)}",
            "refs": refs}


async def deliver(session_factory, *, limit: int = DELIVER_LIMIT,
                  post: Optional[Callable[[dict], Any]] = None) -> dict:
    """One pass: post pending rows to Flows in id order until one is not accepted.

    ``post(body) -> status code`` is injectable for tests; by default it posts to
    ``VEXA_FLOWS_API_URL/events`` with ``VEXA_FLOWS_API_KEY``. Without a Flows address nothing is
    posted and the rows stay pending."""
    from sqlalchemy import select, update

    from .sessions.models import MeetingPlannedOutbox as O

    base, key = _flows()
    if post is None and not base:
        return {"delivered": 0, "pending": True, "reason": "Flows is not configured"}
    client = None
    if post is None:
        import httpx

        client = httpx.AsyncClient(timeout=TIMEOUT_S)
        headers = {"content-type": "application/json"}
        if key:
            headers["X-Flows-Operator-Key"] = key

        async def post(body: dict) -> int:          # noqa: F811 — the default transport
            r = await client.post(f"{base}/events", content=json.dumps(body), headers=headers)
            return r.status_code

    # Read, post, mark — three steps, no session held across the HTTP calls (the tx-scope rule).
    # Single-flight across replicas is the caller's (`__main__`'s guarded loop); a second drain
    # running anyway can only deliver a row twice, which `source_event_id` makes one fact.
    async with session_factory() as db:
        rows = (await db.execute(
            select(O.id, O.event).where(O.delivered.is_(False)).order_by(O.id).limit(limit)
        )).all()
    delivered: list[int] = []
    result: dict = {}
    try:
        for row_id, event in rows:
            try:
                status = await post(body_of(row_id, event))
            except Exception:  # noqa: BLE001 — a transport error is "not yet", never lost
                result = {"reason": "Flows unavailable"}
                break
            if not 200 <= int(status) < 300:
                result = {"status": int(status)}
                break
            delivered.append(row_id)
    finally:
        if client is not None:
            await client.aclose()
    if delivered:
        async with session_factory() as db:
            await db.execute(update(O).where(O.id.in_(delivered)).values(
                delivered=True, delivered_at=datetime.now(timezone.utc).replace(tzinfo=None)))
            await db.commit()
    return {"delivered": len(delivered),
            "pending": bool(result) or len(rows) == limit, **result}
