"""planned_sync.py — a room's planned call follows its creator's calendar (rooms, PLAN-8H 8.4).

The creator's calendar event is the source of truth for a room call's time. When the calendar sensor
reads a change to one of the owner's events (`calendar.event.updated|cancelled`), this hands it to
agent-api's internal room door AS the owner (`POST /api/rooms/calendar-sync`, X-User-Id + the internal
secret), which asks meeting-api to move the matching planned call, or mark it cancelled. Only the
owner's own room calls whose `calendar_event_ref` is that event change; every other row is untouched.

Read-only toward the calendar: nothing is written to any calendar and nobody is told anything here.
A failure is logged and never stops the sensor pass: the next change reconciles again.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

_log = logging.getLogger("flows.planned_sync")
EVENTS = {"calendar.event.updated": "updated", "calendar.event.cancelled": "cancelled"}
PATH = "/api/rooms/calendar-sync"


def body_for(event_type: str, refs: dict) -> Optional[dict]:
    """The sync request for one sensor change, or None when it is not one."""
    status = EVENTS.get(event_type)
    if not status or not refs.get("uid") or not refs.get("event_id"):
        return None
    out = {"event_id": str(refs["event_id"]), "status": status}
    if status == "updated":
        for k in ("start", "end"):
            if refs.get(k):
                out[k] = str(refs[k])
    return out


def on_change(event_type: str, refs: dict, *, post: Optional[Callable] = None) -> Optional[int]:
    """Hand one change on; the HTTP status, or None when it is not a sync. Never raises."""
    body = body_for(event_type, refs)
    if body is None:
        return None
    try:
        if post is None:
            from flows_steps import common  # noqa: PLC0415
            code, _ = common.http("POST", f"{common.agent_door()}{PATH}",
                                  common.door_headers(refs["uid"]),
                                  body)
        else:
            code = post(str(refs["uid"]), PATH, body)
        return int(code or 0)
    except Exception as exc:  # noqa: BLE001 — a sync never stops the sensor pass
        _log.warning("planned call not synced from the calendar (%s)", type(exc).__name__)
        return 0
