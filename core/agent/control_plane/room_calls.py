"""room_calls.py — a room plans a call (rooms, decision F1, R1 1.4).

A planned call is a meeting row meeting-api owns (`POST /meetings`, an intent status, no bot:
`auto_join: false`), naming the room that owns it, the room's workspace, its agenda page and its
invitees. **The creator's calendar is the source of truth for the time and the invitees**: this
module never sends an invitation. It returns the calendar event the creator's own click sends (the
existing `calendar_write` route, as the creator) and the `.ics` address for a creator with no
calendar connected; once the event exists its id is recorded on the row (`calendar_event_ref`), and
the calendar sensor keeps the row in step from then on.

Calls meeting-api over the internal tier AS the creator (`X-User-Id` + `X-Internal-Secret`), with the
room's workspace as the one workspace this write may bind (`X-User-Writable-Workspaces`), which the
route has checked the creator may write.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional


def http(method: str, url: str, headers: dict, body: Optional[dict] = None, timeout: float = 8.0):
    """``(status, json | text)``; ``(0, reason)`` when meeting-api could not be reached."""
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode() or "null"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw
    except Exception as exc:  # noqa: BLE001 — unreachable is a typed answer, never a raise
        return 0, type(exc).__name__


def _headers(subject: str, secret: str, workspace: str) -> dict:
    # agent-api decided `rooms` is on for this caller (its features layer, the company's included):
    # said to meeting-api on the internal tier, so meeting-api does not need its own switch
    h = {"X-User-Id": str(subject), "X-Internal-Secret": secret, "X-User-Features": "rooms"}
    if workspace:
        h["X-User-Writable-Workspaces"] = workspace
        h["X-User-Workspaces"] = workspace
    return h


def plan(base: str, secret: str, *, subject: str, room: dict, session_id: str, title: str, start: str,
         end: str = "", invitees: "list | None" = None, agenda: str = ""):
    """Create the planned call on meeting-api. ``(status, row | error)``."""
    body = {"title": title, "scheduled_at": start, "auto_join": False, "room": session_id,
            "workspace_id": room.get("workspace") or None}
    if end:
        body["end_at"] = end
    if invitees:
        body["attendees"] = list(invitees)
    if agenda:
        body["agenda"] = agenda
    return http("POST", f"{base.rstrip('/')}/meetings", _headers(subject, secret, room.get("workspace") or ""),
                body)


def record_event(base: str, secret: str, *, subject: str, meeting_id: int, event_ref: str):
    """Record the calendar event the creator's click made as the row's source of truth."""
    return http("PATCH", f"{base.rstrip('/')}/meetings/{int(meeting_id)}", _headers(subject, secret, ""),
                {"calendar_event_ref": event_ref})


def sync(base: str, secret: str, *, subject: str, body: dict):
    """A change to the creator's calendar event, handed on to meeting-api AS the creator
    (`POST /meetings/calendar-sync`): their room calls that name that event follow it."""
    return http("POST", f"{base.rstrip('/')}/meetings/calendar-sync", _headers(subject, secret, ""), body)


def calendar_draft(row: dict, *, room_title: str = "") -> dict:
    """The event the creator's own calendar sends, on their click — never sent from here."""
    data = row.get("data") or {}
    return {"summary": data.get("title") or room_title or "Call", "start": data.get("scheduled_at"),
            "end": data.get("end_at"), "attendees": list(data.get("attendees") or []),
            "description": ("Agenda: " + data["agenda"]) if data.get("agenda") else ""}
