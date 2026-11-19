"""Sensor grants: a person lets flows' sensors read ONE calendar connection of theirs, for a while.

The sensors role key is one key for the whole deployment, so it cannot be what decides whose calendar
is read. A grant does: a row the person issues through their own signed identity (the agent role
with the gateway's X-Vexa-Identity for a watched, private turn, or the terminal's human role),
naming the person, one ready `google_calendar` connection, the scope `calendar.read` and an expiry
of at most `MAX_DAYS`. The sensor route reads a calendar only under a live grant whose person is the
sensor's owner and whose connection is the one read; a grant the person revoked, one past its
expiry, and one for another connection or another person read nothing. Disconnecting or deleting the
connection ends every grant on it, because the read also needs the connection ready.

`adopt` is the upgrade step for calendars a sensor was already reading: once per state directory,
every (person, connection) the broker's own audit shows a successful sensor read for in the last
`ADOPT_WINDOW_S`, on a connection still ready, gets a grant of `ADOPT_DAYS`, marked
`issued_via='upgrade'`. Only the broker's own audit trail decides it, never a caller.
"""
from __future__ import annotations

import time
import uuid
from typing import Optional

SCOPE = "calendar.read"
MAX_DAYS = 90
DAY_S = 86400.0
ADOPT_WINDOW_S = 14 * DAY_S
ADOPT_DAYS = 30
_ADOPTED = "sensor_grants_adopted"


def status(row: dict, now: Optional[float] = None) -> str:
    now = time.time() if now is None else now
    if row.get("revoked"):
        return "revoked"
    if float(row["expires"]) <= now:
        return "expired"
    return "active"


def view(row: dict, now: Optional[float] = None) -> dict:
    """credential-broker.v1 `SensorGrant`."""
    return {"grant_id": row["id"], "connection_id": row["connection"], "scope": row["scope"],
            "created": row["created"], "expires": row["expires"], "status": status(row, now),
            "issued_via": row.get("issued_via") or "person"}


def issue(b, actor: str, connection: str, *, days: int, via: str = "person",
          now: Optional[float] = None) -> dict:
    """A new live grant on `connection`; any earlier live grant on it is revoked (one live grant per
    person and connection)."""
    now = time.time() if now is None else now
    days = max(1, min(int(days), MAX_DAYS))
    gid = uuid.uuid4().hex
    with b.lock:
        b.sql("UPDATE sensor_grants SET revoked=? WHERE actor=? AND connection=? AND revoked=0",
              (now, actor, connection))
        b.sql("INSERT INTO sensor_grants(id,actor,connection,scope,created,expires,revoked,issued_via)"
              " VALUES (?,?,?,?,?,?,0,?)", (gid, actor, connection, SCOPE, now, now + days * DAY_S, via))
    return b.sql("SELECT * FROM sensor_grants WHERE id=?", (gid,), one=True)


def latest(b, actor: str, connection: str) -> Optional[dict]:
    """The newest grant this person gave on this connection, live or not."""
    return b.sql("SELECT * FROM sensor_grants WHERE actor=? AND connection=? AND scope=?"
                 " ORDER BY created DESC", (actor, connection, SCOPE), one=True)


def granted_connection(b, actor: str, now: Optional[float] = None) -> Optional[str]:
    """The ready calendar connection with this person's newest LIVE grant, or None."""
    now = time.time() if now is None else now
    row = b.sql("SELECT g.connection FROM sensor_grants g JOIN connections c ON c.id=g.connection"
                " WHERE g.actor=? AND c.actor=? AND g.scope=? AND g.revoked=0 AND g.expires>?"
                " AND c.status='ready' AND c.provider='google_calendar' ORDER BY g.created DESC",
                (actor, actor, SCOPE, now), one=True)
    return row["connection"] if row else None


def adopt(b, now: Optional[float] = None) -> int:
    """The upgrade step (module docstring). Returns how many grants it issued; 0 after the first run."""
    now = time.time() if now is None else now
    with b.lock:
        if b.sql("SELECT 1 FROM broker_meta WHERE key=?", (_ADOPTED,), one=True):
            return 0
        pairs = b.sql("SELECT DISTINCT a.actor AS actor, a.connection AS connection FROM audit a"
                      " JOIN connections c ON c.id=a.connection AND c.actor=a.actor"
                      " WHERE a.action='calendar.changes' AND a.outcome='success' AND a.at>=?"
                      " AND c.status='ready' AND c.provider='google_calendar'",
                      (now - ADOPT_WINDOW_S,), rows=True)
        issued = 0
        for p in pairs:
            if latest(b, p["actor"], p["connection"]) is None:
                issue(b, p["actor"], p["connection"], days=ADOPT_DAYS, via="upgrade", now=now)
                issued += 1
        b.sql("INSERT OR REPLACE INTO broker_meta(key, value) VALUES (?, ?)", (_ADOPTED, str(now)))
    return issued
