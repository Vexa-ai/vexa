"""The sensor route and the person's sensor grants (credential-broker.v1).

flows' sensor pool (ADR-0050) turns a person's calendar changes into events with no model call
(`/api/internal/sensor/calendar-changes`, role `sensors`). The token stays here: the sensor names
the person and its cursor, the broker refreshes the person's Google token if it must, reads
`events.list` incrementally (`syncToken`) and answers normalised events and the next cursor — never a
credential. Read-only by construction: the only provider call behind this route is a GET of the
person's primary calendar. It carries no X-Vexa-Identity (a sensor runs with nobody in the loop), and
its assertion's actor must be the person it names, so every read is audited under that person as
`calendar.changes`.

WHO DECIDES WHOSE CALENDAR IS READ: the person, not the sensors key. A read needs a live sensor grant
(`sensor_grants.py`) that the person issued on the connection read: `POST /api/sensor-grants`, which
only the agent role (the gateway's signature over the person, in a watched, private turn) and the
human role (the terminal, for the signed-in person) reach. The sensors key alone reads nobody's
calendar; with it, flows reads only the calendars their people granted, until the grant expires or
the person revokes it (`POST /api/sensor-grants/{id}/revoke`). A read without one is a 409 with
reason `sensor_grant_required`, audited `refused`.
"""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, HTTPException, Request

from . import providers, sensor_grants
from .broker import Broker
from .faults import UpstreamFault
from .models import CalendarChangesBody, SensorGrantBody
from .reasons import ReasonedError

_NO_GRANT = {
    "none": "This person has not let their sensors read this calendar",
    "revoked": "This person revoked their sensors' access to this calendar",
    "expired": "This person's grant for their sensors to read this calendar has expired",
}


def build(b: Broker) -> APIRouter:
    router = APIRouter()

    @router.post("/api/internal/sensor/calendar-changes")
    def calendar_changes(request: Request, body: CalendarChangesBody):
        who = b.identity(request, {"sensors"})
        if str(who["actor"]) != body.owner:
            raise HTTPException(403, "A sensor reads only the person its request is signed for")
        operation = uuid.uuid4().hex
        with b.lock:
            cid = body.connection_id or sensor_grants.granted_connection(b, body.owner)
            if cid:
                row = b.sql("SELECT * FROM connections WHERE id=? AND actor=? AND status='ready' AND "
                            "provider='google_calendar'", (cid, body.owner), one=True)
            else:
                row = b.sql("SELECT * FROM connections WHERE actor=? AND status='ready' AND "
                            "provider='google_calendar' ORDER BY created DESC", (body.owner,), one=True)
            if not row:
                raise HTTPException(409, "No ready calendar connection for this person")
            cid = row["id"]
            grant = sensor_grants.latest(b, body.owner, cid)
            state = sensor_grants.status(grant) if grant else "none"
            if state != "active":
                b.audit(who, cid, "calendar.changes", "refused", operation=operation, version=row["version"])
                raise ReasonedError(409, _NO_GRANT[state], "sensor_grant_required")
            b.audit(who, cid, "calendar.changes", "requested", operation=operation, version=row["version"])
            try:
                value = b.must_get(cid, row["version"], owner=body.owner)["value"]
                value = b.refreshed(who, cid, row, value, operation)
            except (providers.ProviderError, UpstreamFault, HTTPException):
                b.audit(who, cid, "calendar.changes", "failed", operation=operation, version=row["version"])
                raise
        try:
            result = providers.calendar_changes(value, body.cursor)
        except (providers.ProviderError, UpstreamFault):
            b.audit(who, cid, "calendar.changes", "failed", operation=operation, version=row["version"])
            raise
        b.audit(who, cid, "calendar.changes", "success", operation=operation, version=row["version"])
        return {"connection_id": cid, **result}

    @router.post("/api/sensor-grants")
    def grant_issue(request: Request, body: SensorGrantBody):
        """The person lets their sensors read one ready calendar connection (the newest when none is
        named) for `days`. Replaces any live grant on that connection."""
        who = b.identity(request, {"agent", "human"})
        actor = str(who["actor"])
        if body.connection_id:
            row = b.sql("SELECT * FROM connections WHERE id=? AND actor=? AND status='ready' AND "
                        "provider='google_calendar'", (body.connection_id, actor), one=True)
        else:
            row = b.sql("SELECT * FROM connections WHERE actor=? AND status='ready' AND "
                        "provider='google_calendar' ORDER BY created DESC", (actor,), one=True)
        if not row:
            raise HTTPException(409, "No ready calendar connection for this person")
        b.audit(who, row["id"], "sensor_grant.issue", "requested")
        grant = sensor_grants.issue(b, actor, row["id"], days=body.days)
        b.audit(who, row["id"], "sensor_grant.issue", "stored", receipt=grant["id"])
        return sensor_grants.view(grant)

    @router.get("/api/sensor-grants")
    def grant_list(request: Request):
        """The person's sensor grants, newest first: active, revoked or expired."""
        who = b.identity(request, {"agent", "human"}, unwatched_ok=True)
        rows = b.sql("SELECT * FROM sensor_grants WHERE actor=? ORDER BY created DESC LIMIT 100",
                     (str(who["actor"]),), rows=True)
        now = time.time()
        return {"grants": [sensor_grants.view(r, now) for r in rows]}

    @router.post("/api/sensor-grants/{grant_id}/revoke")
    def grant_revoke(request: Request, grant_id: str):
        """End one of the person's grants now. Revoking only ever narrows, so a worker with nobody in
        the loop may do it too. Another person's grant id is a 404."""
        who = b.identity(request, {"agent", "human"}, unwatched_ok=True)
        row = b.sql("SELECT * FROM sensor_grants WHERE id=? AND actor=?", (grant_id, str(who["actor"])), one=True)
        if not row:
            raise HTTPException(404, "Grant not found")
        if not row["revoked"]:
            b.sql("UPDATE sensor_grants SET revoked=? WHERE id=?", (time.time(), grant_id))
        b.audit(who, row["connection"], "sensor_grant.revoke", "stored", receipt=grant_id)
        return sensor_grants.view(b.sql("SELECT * FROM sensor_grants WHERE id=?", (grant_id,), one=True))

    return router
