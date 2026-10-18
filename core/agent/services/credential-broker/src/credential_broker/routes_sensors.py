"""The sensor route (credential-broker.v1 `/api/internal/sensor/calendar-changes`, role `sensors`).

flows' sensor pool (ADR-0050) turns a person's calendar changes into events with no model call. The
token stays here: the sensor names the person and its cursor, the broker refreshes the person's
Google token if it must, reads `events.list` incrementally (`syncToken`) and answers normalised
events and the next cursor — never a credential. Read-only by construction: the only provider call
behind this route is a GET of the person's primary calendar. Only the `sensors` role reaches it, it
carries no X-Vexa-Identity (a sensor runs with nobody in the loop), and its assertion's actor must be
the person it names, so a key can only read the person each request is signed for and every read is
audited under that person as `calendar.changes`.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request

from . import providers
from .broker import Broker
from .faults import UpstreamFault
from .models import CalendarChangesBody


def build(b: Broker) -> APIRouter:
    router = APIRouter()

    @router.post("/api/internal/sensor/calendar-changes")
    def calendar_changes(request: Request, body: CalendarChangesBody):
        who = b.identity(request, {"sensors"})
        if str(who["actor"]) != body.owner:
            raise HTTPException(403, "A sensor reads only the person its request is signed for")
        operation = uuid.uuid4().hex
        with b.lock:
            if body.connection_id:
                row = b.sql("SELECT * FROM connections WHERE id=? AND actor=? AND status='ready' AND "
                            "provider='google_calendar'", (body.connection_id, body.owner), one=True)
            else:
                row = b.sql("SELECT * FROM connections WHERE actor=? AND status='ready' AND "
                            "provider='google_calendar' ORDER BY created DESC", (body.owner,), one=True)
            if not row:
                raise HTTPException(409, "No ready calendar connection for this person")
            cid = row["id"]
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

    return router
