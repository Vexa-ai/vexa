"""The one pool every sensor runs in (ADR-0050 decisions 1.6 and 6).

* :func:`run_due` — tick every due ACTIVE sensor once: claim it with a lease, call its kind's `tick`
  with the stored cursor, keep only the changes that pass the sensor's own filter, admit each
  through flows (deduped `sensor:<scope>/<name>:<key>`), checkpoint the cursor only when it moved,
  schedule the next tick with jitter. A failed tick counts; at the kind's `degrade_after` the sensor
  reads `degraded` with the reason, and backs off.
* :func:`dry_run` — `sensor_test`: the same read and the same filter, with nothing stored and
  nothing admitted. Answers what WOULD be emitted and what was dropped, and why.
* :func:`health` — what the routine strip, the chat header and `sensor_list` show.

Scheduling is a due-time column with leases, not the in-memory heap of the ADR's spike: at this
deployment's size one pass a second over an indexed `next_due_at` is the same work, and the heap is a
later optimisation behind the same interface.
"""
from __future__ import annotations

import json
import logging
import random
import time
from typing import Callable, Iterable

from flows.triggers import envelope, evaluate, parse_filter, TriggerError

from .base import Change, SensorFault, SensorKind, SensorRow, Tick

_log = logging.getLogger("flows.sensors")

LEASE_S = 120.0
PASS_MAX = 50
BACKOFF_MAX_S = 3600.0
#: How many recent events a sensor remembers for its health line.
LAST_EVENTS = 5
#: How many would-be events and drops a test answers at most.
TEST_MAX = 50

Emit = Callable[[str, str, dict], int]

COLS = ("scope, name, kind, connection_id, filter, interval_s, version, tested_version, cursor, "
        "status, mode, failures, reason, last_tick_at, last_change_at, next_due_at")


def row(r) -> SensorRow:
    (scope, name, kind, conn, flt, interval, version, tested, cursor, status, mode, failures, reason,
     last_tick, last_change, due) = r
    return SensorRow(scope=scope, name=name, kind=kind, connection_id=conn, filter=flt,
                     interval_s=float(interval or 60), version=int(version or 1),
                     tested_version=tested, cursor=cursor, status=status, mode=mode or "poll",
                     failures=int(failures or 0), reason=reason, last_tick_at=last_tick,
                     last_change_at=last_change, next_due_at=float(due or 0))


def _interval(kind: SensorKind, s: SensorRow) -> float:
    return max(float(s.interval_s or 0), float(kind.min_interval_s))


def _jitter(interval: float) -> float:
    return interval * (0.9 + 0.2 * random.random())


def sort_changes(kind: SensorKind, s: SensorRow, changes: Iterable) -> tuple[list, list]:
    """(kept, dropped) — every change through the sensor's filter. A change the kind returned in the
    wrong shape is dropped with that reason, never emitted."""
    try:
        tree = parse_filter(s.filter or "")
    except TriggerError as e:
        return [], [{"reason": f"the filter cannot be read: {e}"}]
    kept, dropped = [], []
    for ch in changes or []:
        if not isinstance(ch, Change):
            dropped.append({"reason": "not a change"})
            continue
        refs = dict(ch.refs or {})
        if s.uid:
            named = refs.get("uid")
            if named not in (None, "") and str(named) != s.uid:
                dropped.append({"event_type": ch.event_type, "key": ch.dedupe_key,
                                "reason": "names_another_person"})
                continue
            refs["uid"] = s.uid
        if tree is not None and not evaluate(tree, envelope(ch.event_type, refs)):
            dropped.append({"event_type": ch.event_type, "key": ch.dedupe_key, "reason": "filter",
                            **_label(refs)})
            continue
        kept.append(Change(ch.event_type, ch.dedupe_key, refs))
    return kept, dropped


def _label(refs: dict) -> dict:
    out = {k: refs[k] for k in ("title", "start", "subject", "from") if refs.get(k) not in (None, "")}
    return {k: (str(v)[:120] if isinstance(v, str) else v) for k, v in out.items()}


def tick_one(db, kind: SensorKind, s: SensorRow, emit: Emit, now: float) -> dict:
    """One sensor, one read. Never raises: a failure is counted on the row."""
    if not kind.allowed_in(s.scope):
        return _failed(db, kind, s, "kind_not_allowed_in_scope", now, stop=True)
    try:
        result = kind.tick(s, s.cursor)
        if not isinstance(result, Tick):
            raise SensorFault("the sensor answered nothing readable")
    except SensorFault as e:
        return _failed(db, kind, s, e.reason, now, stop=not e.retryable)
    except Exception as e:  # noqa: BLE001 — a kind's bug is that sensor's fault, never the loop's
        _log.exception("sensor %s tick raised", s.sensor_id)
        return _failed(db, kind, s, f"internal: {type(e).__name__}", now)
    kept, _dropped = sort_changes(kind, s, result.changes)
    emitted = 0
    for ch in kept:
        refs = {**ch.refs, "_sensor": s.sensor_id}
        emitted += int(emit(ch.event_type, f"sensor:{s.sensor_id}:{ch.dedupe_key}", refs) or 0)
    sets = ["lease_until = NULL", "next_due_at = :due", "last_tick_at = :now", "failures = 0"]
    params = {"due": now + _jitter(_interval(kind, s)), "now": now, "sc": s.scope, "n": s.name}
    if s.status == "degraded" or s.failures:
        sets += ["status = 'active'", "reason = NULL"]
    if result.resync:
        sets.append("cursor = NULL")
    elif result.cursor is not None and result.cursor != s.cursor:
        sets.append("cursor = :cursor")
        params["cursor"] = result.cursor
    if kept:
        sets += ["last_change_at = :now", "last_events = :last"]
        params["last"] = json.dumps([{"type": c.event_type, "at": now, **_label(c.refs)}
                                     for c in kept[-LAST_EVENTS:]])
    db.execute(f"UPDATE sensor SET {', '.join(sets)} WHERE scope = :sc AND name = :n", params)
    return {"sensor": s.sensor_id, "changes": len(result.changes or []), "kept": len(kept),
            "admitted": emitted}


def _failed(db, kind: SensorKind, s: SensorRow, reason: str, now: float, *, stop: bool = False) -> dict:
    n = s.failures + 1
    status = "stopped" if stop else ("degraded" if n >= kind.degrade_after else "active")
    wait = min(BACKOFF_MAX_S, _interval(kind, s) * (2 ** min(n, 10)))
    db.execute("UPDATE sensor SET failures = :n, reason = :r, status = :st, lease_until = NULL, "
               "last_tick_at = :now, next_due_at = :due WHERE scope = :sc AND name = :nm",
               {"n": n, "r": str(reason)[:300], "st": status, "now": now, "due": now + wait,
                "sc": s.scope, "nm": s.name})
    _log.warning("sensor %s failed (%d in a row): %s", s.sensor_id, n, reason)
    return {"sensor": s.sensor_id, "failed": reason, "status": status}


def _claim(db, now: float, limit: int) -> list[SensorRow]:
    rows = db.execute(f"SELECT {COLS} FROM sensor WHERE status IN ('active','degraded') AND "
                      "next_due_at <= :now AND (lease_until IS NULL OR lease_until < :now) "
                      "ORDER BY next_due_at LIMIT :n", {"now": now, "n": int(limit)})
    out = []
    for r in rows:
        got = db.execute("UPDATE sensor SET lease_until = :until WHERE scope = :sc AND name = :n AND "
                         "(lease_until IS NULL OR lease_until < :now) RETURNING name",
                         {"until": now + LEASE_S, "sc": r[0], "n": r[1], "now": now})
        if got:
            out.append(row(r))
    return out


def run_due(db, kinds: "dict[str, SensorKind]", emit: Emit, now: "float | None" = None,
            *, limit: int = PASS_MAX) -> list[dict]:
    """Tick every due sensor of a known kind once. Returns one result per sensor ticked."""
    now = time.time() if now is None else now
    out = []
    for s in _claim(db, now, limit):
        kind = kinds.get(s.kind)
        if kind is None:
            db.execute("UPDATE sensor SET lease_until = NULL WHERE scope = :sc AND name = :n",
                       {"sc": s.scope, "n": s.name})
            continue
        out.append(tick_one(db, kind, s, emit, now))
    return out


def dry_run(kind: SensorKind, s: SensorRow) -> dict:
    """`sensor_test`: read as a first tick would (no cursor), filter, and answer — store nothing,
    admit nothing. `{ok, would_emit, dropped, counts}` or `{ok: false, reason}`."""
    if not kind.allowed_in(s.scope):
        return {"ok": False, "reason": "kind_not_allowed_in_scope"}
    probe = SensorRow(**{**s.__dict__, "cursor": None})
    try:
        result = kind.tick(probe, None)
    except SensorFault as e:
        return {"ok": False, "reason": e.reason}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"internal: {type(e).__name__}"}
    kept, dropped = sort_changes(kind, s, getattr(result, "changes", []))
    return {"ok": True,
            "would_emit": [{"event_type": c.event_type, "key": c.dedupe_key, **_label(c.refs),
                            **{k: c.refs[k] for k in ("video_url", "self_response", "status")
                               if c.refs.get(k) not in (None, "")}} for c in kept[:TEST_MAX]],
            "dropped": dropped[:TEST_MAX],
            "counts": {"read": len(getattr(result, "changes", []) or []), "would_emit": len(kept),
                       "dropped": len(dropped)}}


def health(db, scope: str, name: str = "") -> list[dict]:
    """A person's sensors (and the company's when `scope` is instance), as the strip shows them."""
    rows = db.execute(f"SELECT {COLS}, last_events FROM sensor WHERE scope = :sc"
                      + (" AND name = :n" if name else "") + " ORDER BY name",
                      {"sc": scope, **({"n": name} if name else {})})
    out = []
    for r in rows:
        s = row(r[:-1])
        try:
            last = json.loads(r[-1] or "[]")
        except ValueError:
            last = []
        out.append({"name": s.name, "kind": s.kind, "status": s.status, "mode": s.mode,
                    "filter": s.filter, "interval_s": s.interval_s, "version": s.version,
                    "tested": s.tested_version == s.version, "failures": s.failures,
                    "reason": s.reason, "last_tick_at": s.last_tick_at,
                    "last_change_at": s.last_change_at,
                    "next_check_at": s.next_due_at if s.status in ("active", "degraded") else None,
                    "last_events": last})
    return out
