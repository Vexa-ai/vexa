"""Keyed schedules — how "the bot follows the calendar event" works (ADR-0050 decision 3).

A `relative` trigger (`{kind: relative, event: "calendar.event.*", filter, at: "payload.start - 60s",
key: "payload.event_id"}`) does not start a run when its event arrives. It keeps ONE pending fire per
key in `keyed_schedule`:

* an event that passes the filter UPSERTS the row — a new `fire_at`, `version + 1`. The meeting moved:
  the row moved. Nothing else changes, so a duplicate delivery is a no-op;
* an event that no longer passes (declined, link removed) or a `*.cancelled` CANCELS it;
* the worker fires due rows (`fire_due`) into an ordinary reaction keyed
  `at:<scope>/<flow>/<key>:<fire_at>`, so a slot fires at most once by the reaction table's own
  unique constraint. A row moved AFTER it fired re-arms (the meeting moved later); a row not moved
  never fires twice.

The run's refs are the event's refs as the last upsert saw them (`payload`), plus `uid`, `_key` and
`fire_at`, so a step reads `meeting_url` from `{{payload.video_url}}` the same way it would have read
the event itself. Engine core: stdlib only, the DB is `(sql, params) -> rows`.
"""
from __future__ import annotations

import logging

from .clock import Clock
from .db import DB, dumps, loads
from .registry import Flow, Registry

_log = logging.getLogger(__name__)

#: How late a due row may still fire, counted from its slot or from when it was armed if later. Older
#: rows are `missed`: a bot 20 minutes into a meeting it was meant to open is not what the person asked
#: for, unless the invitation itself arrived after the start.
CATCHUP_S = 600.0
#: Pending rows one person may hold in one scope (`keyed_schedules_per_user`) — their own flows, or
#: their share of a company flow — and the ceiling a company flow holds for everyone together.
PER_USER = 500
COMPANY_MAX = 20000
KEYED_EVENT = "keyed.due"


def held(db: DB, scope: str, person: "str | None" = None) -> int:
    sql = "SELECT COUNT(*) FROM keyed_schedule WHERE scope = :sc AND status = 'armed'"
    params = {"sc": scope}
    if person is not None:
        sql += " AND person = :p"
        params["p"] = person
    rows = db.execute(sql, params)
    return int(rows[0][0] or 0) if rows else 0


def person_of(flow: Flow, payload: dict) -> str:
    """Whom a pending fire is for: the flow's owner, or for a company flow the person the event names."""
    if flow.owner:
        return flow.owner
    return str((payload or {}).get("uid") or (payload or {}).get("organizer") or "")[:200]


def upsert(db: DB, clock: Clock, flow: Flow, *, key: str, fire_at: float, event_type: str,
           payload: dict, per_user: int = PER_USER) -> str:
    """Arm or move the row for ``key``. Returns what happened: `armed`, `moved`, `rearmed`,
    `unchanged` or `refused` (the person's cap)."""
    now = clock.now()
    rows = db.execute("SELECT fire_at, status, version, fired_for FROM keyed_schedule "
                      "WHERE scope = :sc AND flow = :f AND key = :k",
                      {"sc": flow.scope, "f": flow.name, "k": key})
    if not rows:
        person = person_of(flow, payload)
        if per_user >= 0 and held(db, flow.scope, person) >= per_user:
            _log.warning("keyed schedule refused for %s: the person's %d pending (cap %d)", flow.ident,
                         per_user, per_user)
            return "refused"
        if not flow.owner and held(db, flow.scope) >= COMPANY_MAX:
            _log.warning("keyed schedule refused for %s: company ceiling %d reached", flow.ident,
                         COMPANY_MAX)
            return "refused"
        db.execute("INSERT INTO keyed_schedule (scope, flow, key, fire_at, status, version, "
                   "event_type, person, payload, fired_for, updated_at) VALUES (:sc, :f, :k, :at, "
                   "'armed', 1, :et, :who, :p, NULL, :now)",
                   {"sc": flow.scope, "f": flow.name, "k": key, "at": float(fire_at),
                    "et": event_type, "who": person, "p": dumps(payload), "now": now})
        return "armed"
    old_at, status, version, fired_for = rows[0]
    same_time = abs(float(old_at) - float(fire_at)) < 1.0
    if status == "fired" and same_time:
        db.execute("UPDATE keyed_schedule SET payload = :p, event_type = :et, updated_at = :now "
                   "WHERE scope = :sc AND flow = :f AND key = :k",
                   {"p": dumps(payload), "et": event_type, "now": now, "sc": flow.scope,
                    "f": flow.name, "k": key})
        return "unchanged"
    if status == "armed" and same_time:
        db.execute("UPDATE keyed_schedule SET payload = :p, event_type = :et, updated_at = :now "
                   "WHERE scope = :sc AND flow = :f AND key = :k",
                   {"p": dumps(payload), "et": event_type, "now": now, "sc": flow.scope,
                    "f": flow.name, "k": key})
        return "unchanged"
    db.execute("UPDATE keyed_schedule SET fire_at = :at, status = 'armed', version = :v, "
               "payload = :p, event_type = :et, updated_at = :now "
               "WHERE scope = :sc AND flow = :f AND key = :k",
               {"at": float(fire_at), "v": int(version or 0) + 1, "p": dumps(payload),
                "et": event_type, "now": now, "sc": flow.scope, "f": flow.name, "k": key})
    return "rearmed" if status == "fired" else "moved"


def cancel(db: DB, clock: Clock, flow: Flow, *, key: str) -> bool:
    """Cancel the pending fire for ``key``. True when one was pending."""
    rows = db.execute("UPDATE keyed_schedule SET status = 'cancelled', updated_at = :now "
                      "WHERE scope = :sc AND flow = :f AND key = :k AND status = 'armed' "
                      "RETURNING key", {"now": clock.now(), "sc": flow.scope, "f": flow.name, "k": key})
    return bool(rows)


def upcoming(db: DB, scope: str, flow_name: str, *, limit: int = 3) -> list[dict]:
    """The next pending fires of one flow, soonest first — what the routine strip shows."""
    rows = db.execute("SELECT key, fire_at, payload FROM keyed_schedule WHERE scope = :sc AND "
                      "flow = :f AND status = 'armed' ORDER BY fire_at LIMIT :n",
                      {"sc": scope, "f": flow_name, "n": int(limit)})
    out = []
    for key, at, payload in rows:
        p = loads(payload) if payload else {}
        out.append({"key": key, "fire_at": float(at),
                    **({"title": str(p.get("title"))[:120]} if p.get("title") else {})})
    return out


def slot_key(flow: Flow, key: str, fire_at: float) -> str:
    return f"at:{flow.scope}/{flow.name}/{key}:{int(fire_at)}"


def fire_due(db: DB, registry: Registry, clock: Clock, *, catchup_s: float = CATCHUP_S,
             admissible=None, **limits) -> int:
    """Fire every armed row whose time has come into one reaction on the flow's newest chosen version.
    ``admissible(flow, refs) -> bool`` is the daily-cap check (the caller's), so a keyed fire counts
    like any other run. Returns reactions created."""
    from .admission import insert
    now = clock.now()
    rows = db.execute("SELECT scope, flow, key, fire_at, event_type, payload, updated_at "
                      "FROM keyed_schedule WHERE status = 'armed' AND fire_at <= :now "
                      "ORDER BY fire_at LIMIT 200", {"now": now})
    created = 0
    for scope, name, key, at, event_type, payload, armed_at in rows:
        flow = registry.newest(scope, name)
        if flow is None:
            continue                  # retired or not loaded: the row waits for its flow, or for cleanup
        # LATENESS COUNTS FROM WHEN THE WORKER COULD FIRST HAVE FIRED IT: the slot, or the moment the
        # row was armed or moved if that came after the slot. An invite forwarded twenty minutes into
        # a meeting arms a row whose time has passed; that is not a miss, it is due now.
        if now - max(float(at), float(armed_at or 0)) > catchup_s:
            db.execute("UPDATE keyed_schedule SET status = 'missed', updated_at = :now WHERE "
                       "scope = :sc AND flow = :f AND key = :k AND status = 'armed'",
                       {"now": now, "sc": scope, "f": name, "k": key})
            _log.warning("keyed fire %s/%s missed by %.0fs", flow.ident, key, now - float(at))
            continue
        refs = {**(loads(payload) if payload else {}), "_key": key, "fire_at": float(at),
                "_event_type": event_type}
        if flow.owner:
            refs["uid"] = flow.owner
        if admissible is not None and not admissible(flow, refs):
            continue
        made = insert(db, clock, flow, key=f"{slot_key(flow, key, float(at))}::{flow.ident}",
                      event_type=KEYED_EVENT, subject_refs=refs)
        db.execute("UPDATE keyed_schedule SET status = 'fired', fired_for = :at, updated_at = :now "
                   "WHERE scope = :sc AND flow = :f AND key = :k AND fire_at = :at",
                   {"at": float(at), "now": now, "sc": scope, "f": name, "k": key})
        created += len(made or [])
    return created
