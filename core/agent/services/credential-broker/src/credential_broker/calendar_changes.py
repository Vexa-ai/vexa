"""Google Calendar changes: create, update and cancel an event on the person's calendar, idempotent
by request key, only with the separate calendar-change grant, and only on events the person can
change. Split out of `providers.py`, which re-exports `calendar_event`, `event_id_for` and
`write_calendar_event`; the routes call them as `providers.<name>`.
"""
from __future__ import annotations

import re
from datetime import datetime

import httpx

from .faults import UpstreamFault
from .provider_base import (CALENDAR_WRITE_SCOPE, ProviderError, _NO_ANSWER, _address, _bearer,
                            _fault_for_status)

_CAL = "https://www.googleapis.com/calendar/v3/calendars/"


def _event_time(t: dict, which: str) -> dict:
    """A CalendarTime as Google's EventDateTime, validated: a timezone-qualified instant, or an
    all-day date."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    if t.get("date") and t.get("date_time"):
        raise ProviderError(f"Give {which} as date_time or date, not both")
    if t.get("date"):
        try:
            datetime.strptime(t["date"], "%Y-%m-%d")
        except ValueError:
            raise ProviderError(f"Invalid {which} date") from None
        return {"date": t["date"]}
    try:
        moment = datetime.fromisoformat(str(t.get("date_time") or "").replace("Z", "+00:00"))
    except ValueError:
        raise ProviderError(f"Invalid {which} time; use ISO 8601") from None
    zone = t.get("time_zone") or ""
    if zone:
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ProviderError(f"Invalid {which} time zone; use an IANA name such as Europe/Lisbon") from None
    elif moment.tzinfo is None:
        raise ProviderError(f"Give {which} with an offset or a time_zone")
    out = {"dateTime": moment.isoformat()}
    if zone:
        out["timeZone"] = zone
    return out


def _span(start: dict, end: dict):
    def key(t):
        if "date" in t:
            return datetime.fromisoformat(t["date"] + "T00:00:00+00:00")
        moment = datetime.fromisoformat(t["dateTime"])
        if moment.tzinfo is None:
            from zoneinfo import ZoneInfo
            moment = moment.replace(tzinfo=ZoneInfo(t["timeZone"]))
        return moment
    if ("date" in start) != ("date" in end):
        raise ProviderError("Start and end must both be all-day dates or both be times")
    if key(end) <= key(start):
        raise ProviderError("The event must end after it starts")


def _cal_request(http, method, url, value, *, params=None, payload=None, unknown: str):
    try:
        response = http.request(method, url, params=params, headers=_bearer(value), json=payload)
    except _NO_ANSWER:
        raise UpstreamFault("provider", "unreachable", unknown) from None
    except httpx.HTTPError:
        raise UpstreamFault("provider", "bad_answer", unknown) from None
    return response


def calendar_event(value, calendar_id: str, event_id: str, *, http) -> dict:
    """The event as the person's calendar holds it, or ProviderError when the id does not resolve."""
    from urllib.parse import quote

    if not re.fullmatch(r"[a-v0-9_]{5,1024}", event_id or ""):
        raise ProviderError("Use an event id from a calendar read")
    response = _cal_request(http, "GET", _CAL + quote(calendar_id, safe="@.") + "/events/" + event_id, value,
                            unknown="Calendar could not be read; retry later")
    if response.status_code in (404, 410):
        raise ProviderError("Event not found in this calendar; use an event id from a calendar read")
    if response.status_code in (400, 401, 403):
        raise ProviderError("Calendar could not be read; check account permissions")
    if response.status_code != 200:
        raise _fault_for_status(response.status_code, "Calendar could not be read; retry later")
    try:
        data = response.json()
    except ValueError:
        raise UpstreamFault("provider", "bad_answer", "Calendar could not be read; retry later") from None
    if data.get("status") == "cancelled":
        raise ProviderError("That event is already cancelled")
    return data


def event_id_for(request_key: str) -> str:
    """A Google event id derived from the request: base32hex characters only (hex is a subset), so
    a create retried after an unknown outcome cannot make a second event — Google refuses the id."""
    import hashlib

    return hashlib.sha256(request_key.encode()).hexdigest()[:40]


def write_calendar_event(value, action: str, calendar_id: str, *, event_id="", fields=None, notify=False,
                         conference=False, request_key="", http=None) -> dict:
    """Create, update or cancel one event. ``fields`` are the CalendarEventBody fields the agent set
    (None = unchanged on update). Update and cancel require an event the person can change: they
    organize it (cancel), or organize it or the event lets guests modify it (update)."""
    from urllib.parse import quote

    if CALENDAR_WRITE_SCOPE not in value.get("scope", "").split():
        raise ProviderError("Calendar change permission required")
    if http is None:
        with httpx.Client(timeout=20, follow_redirects=False) as session:
            return write_calendar_event(value, action, calendar_id, event_id=event_id, fields=fields, notify=notify,
                                        conference=conference, request_key=request_key, http=session)
    fields = fields or {}
    unknown = "Calendar change outcome unknown; check your calendar before retrying"
    params = {"sendUpdates": "all" if notify else "none"}
    events = _CAL + quote(calendar_id, safe="@.") + "/events"
    payload: dict = {}
    if fields.get("title") is not None:
        payload["summary"] = fields["title"]
    for key in ("description", "location"):
        if fields.get(key) is not None:
            payload[key] = fields[key]
    if fields.get("attendees") is not None:
        payload["attendees"] = [{"email": _address(a)} for a in fields["attendees"]]
    if fields.get("start") is not None:
        payload["start"] = _event_time(fields["start"], "start")
    if fields.get("end") is not None:
        payload["end"] = _event_time(fields["end"], "end")

    if action == "calendar.event.create":
        if "start" not in payload or "end" not in payload or not payload.get("summary"):
            raise ProviderError("A new event needs a title, a start and an end")
        _span(payload["start"], payload["end"])
        payload["id"] = event_id_for(request_key)
        if conference:
            payload["conferenceData"] = {"createRequest": {"requestId": payload["id"],
                                                           "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
            params["conferenceDataVersion"] = "1"
        response = _cal_request(http, "POST", events, value, params=params, payload=payload, unknown=unknown)
        if response.status_code == 409:
            raise ProviderError("This request already created an event; check your calendar")
        status = "event_created"
    else:
        current = calendar_event(value, calendar_id, event_id, http=http)
        organizer = bool((current.get("organizer") or {}).get("self"))
        if action == "calendar.event.cancel":
            if not organizer:
                raise ProviderError("Only events this person organizes can be cancelled")
            response = _cal_request(http, "DELETE", events + "/" + event_id, value, params=params, unknown=unknown)
            status = "event_cancelled"
        else:
            if not organizer and not current.get("guestsCanModify"):
                raise ProviderError("This person cannot change that event; they do not organize it")
            if not payload and not conference:
                raise ProviderError("Give at least one field to change")
            start = payload.get("start", current.get("start") or {})
            end = payload.get("end", current.get("end") or {})
            if ("start" in payload or "end" in payload) and start and end:
                _span({k: v for k, v in start.items() if k in ("date", "dateTime", "timeZone")},
                      {k: v for k, v in end.items() if k in ("date", "dateTime", "timeZone")})
            if conference and not current.get("hangoutLink"):
                payload["conferenceData"] = {"createRequest": {"requestId": event_id_for(request_key),
                                                               "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
                params["conferenceDataVersion"] = "1"
            response = _cal_request(http, "PATCH", events + "/" + event_id, value, params=params, payload=payload,
                                    unknown=unknown)
            status = "event_updated"
    if response.status_code in (404, 410):
        raise ProviderError("Event not found in this calendar; use an event id from a calendar read")
    if response.status_code in (400, 401, 403):
        raise ProviderError("Calendar change refused; check the event fields and account permissions")
    if response.status_code not in (200, 201, 204):
        raise _fault_for_status(response.status_code, unknown)
    if status == "event_cancelled":
        return {"status": status, "event_id": event_id, "calendar_id": calendar_id, "notified": bool(notify)}
    try:
        data = response.json()
        return {"status": status, "event_id": data["id"], "calendar_id": calendar_id,
                "html_link": data.get("htmlLink", ""), "meet_link": data.get("hangoutLink", ""),
                "notified": bool(notify)}
    except (ValueError, KeyError, TypeError):
        raise UpstreamFault("provider", "bad_answer", unknown) from None
