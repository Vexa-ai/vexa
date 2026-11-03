"""A person's sensors as rows: make, change, test, enable, pause, delete (ADR-0050 decision 6).

Every change is a new `version`; a version runs only after `test` passed ON THAT VERSION (the
founder's rule: design, then test, then enable). Pure over `(sql, params) -> rows`; flows-api is the
only caller and decides who may touch which scope.
"""
from __future__ import annotations

import re
import time
from typing import Optional

from flows.triggers import TriggerError, parse_filter

from . import pool
from .base import SensorKind, SensorRow

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
#: Sensors one person may hold (`sensors_per_user`).
PER_SCOPE = 20


class SensorError(ValueError):
    """A refusal with a stable `reason` and facts, for a typed 4xx."""

    def __init__(self, reason: str, code: int = 400, **facts):
        super().__init__(reason)
        self.reason, self.code, self.facts = reason, code, facts


def get(db, scope: str, name: str) -> Optional[SensorRow]:
    rows = db.execute(f"SELECT {pool.COLS} FROM sensor WHERE scope = :sc AND name = :n",
                      {"sc": scope, "n": name})
    return pool.row(rows[0]) if rows else None


def _in_scope(k: SensorKind, scope: str) -> None:
    if not k.allowed_in(scope):
        raise SensorError("kind_not_allowed_in_scope", 403, kind=k.kind, scope=scope,
                          allowed=list(k.scopes or ()))


def _check(kinds: dict, kind: str, flt: Optional[str], interval_s: Optional[float],
           scope: str) -> SensorKind:
    k = kinds.get(kind)
    if k is None:
        raise SensorError("unknown_kind", kind=kind, known=sorted(kinds))
    _in_scope(k, scope)
    if flt:
        try:
            parse_filter(flt)
        except TriggerError as e:
            raise SensorError("bad_filter", note=str(e)) from None
    if interval_s is not None and float(interval_s) < k.min_interval_s:
        raise SensorError("interval_too_short", minimum_s=k.min_interval_s)
    return k


def create(db, kinds: dict, scope: str, *, name: str, kind: str, filter: str = "",
           connection_id: str = "", interval_s: Optional[float] = None, by: str = "",
           now: Optional[float] = None) -> SensorRow:
    if not NAME.match(name or ""):
        raise SensorError("bad_name", note="letters, digits, . _ - ; up to 64")
    k = _check(kinds, kind, filter, interval_s, scope)
    if get(db, scope, name):
        raise SensorError("exists", 409, name=name)
    held = db.execute("SELECT COUNT(*) FROM sensor WHERE scope = :sc", {"sc": scope})
    if int(held[0][0] or 0) >= PER_SCOPE:
        raise SensorError("quota_sensors", 403, maximum=PER_SCOPE)
    now = time.time() if now is None else now
    db.execute("INSERT INTO sensor (scope, name, kind, connection_id, filter, interval_s, version, "
               "status, mode, next_due_at, failures, created_by, created_at) VALUES (:sc, :n, :k, "
               ":c, :f, :i, 1, 'draft', 'poll', :now, 0, :by, :now)",
               {"sc": scope, "n": name, "k": kind, "c": connection_id or None, "f": filter or None,
                "i": float(interval_s or k.min_interval_s), "now": now, "by": by or None})
    return get(db, scope, name)


def update(db, kinds: dict, scope: str, name: str, *, filter: Optional[str] = None,
           interval_s: Optional[float] = None, connection_id: Optional[str] = None) -> SensorRow:
    """A change is a new version, back to draft until it is tested again."""
    s = get(db, scope, name)
    if s is None:
        raise SensorError("not_found", 404, name=name)
    _check(kinds, s.kind, filter if filter is not None else s.filter, interval_s, scope)
    db.execute("UPDATE sensor SET filter = :f, interval_s = :i, connection_id = :c, version = :v, "
               "status = 'draft' WHERE scope = :sc AND name = :n",
               {"f": (filter if filter is not None else s.filter) or None,
                "i": float(interval_s if interval_s is not None else s.interval_s),
                "c": (connection_id if connection_id is not None else s.connection_id) or None,
                "v": s.version + 1, "sc": scope, "n": name})
    return get(db, scope, name)


def test(db, kinds: dict, scope: str, name: str, *, now: Optional[float] = None) -> dict:
    """Run the sensor over recent real data with no side effects; on success, mark this version tested."""
    s = get(db, scope, name)
    if s is None:
        raise SensorError("not_found", 404, name=name)
    k = kinds.get(s.kind)
    if k is None:
        raise SensorError("unknown_kind", kind=s.kind, known=sorted(kinds))
    _in_scope(k, s.scope)
    result = pool.dry_run(k, s)
    if result.get("ok"):
        db.execute("UPDATE sensor SET tested_version = :v, tested_at = :now WHERE scope = :sc AND "
                   "name = :n", {"v": s.version, "now": time.time() if now is None else now,
                                 "sc": scope, "n": name})
    return {"name": name, "version": s.version, **result}


def enable(db, scope: str, name: str, *, now: Optional[float] = None) -> SensorRow:
    s = get(db, scope, name)
    if s is None:
        raise SensorError("not_found", 404, name=name)
    if s.tested_version != s.version:
        raise SensorError("test_first", 409, version=s.version, tested_version=s.tested_version,
                          note="run sensor_test on this version, then enable it")
    db.execute("UPDATE sensor SET status = 'active', failures = 0, reason = NULL, next_due_at = :now "
               "WHERE scope = :sc AND name = :n",
               {"now": time.time() if now is None else now, "sc": scope, "n": name})
    return get(db, scope, name)


def pause(db, scope: str, name: str) -> SensorRow:
    if get(db, scope, name) is None:
        raise SensorError("not_found", 404, name=name)
    db.execute("UPDATE sensor SET status = 'stopped', reason = 'paused by its owner' WHERE "
               "scope = :sc AND name = :n", {"sc": scope, "n": name})
    return get(db, scope, name)


def delete(db, scope: str, name: str) -> bool:
    return bool(db.execute("DELETE FROM sensor WHERE scope = :sc AND name = :n RETURNING name",
                           {"sc": scope, "n": name}))
