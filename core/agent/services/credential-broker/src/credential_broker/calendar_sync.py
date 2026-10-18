"""Google Calendar incremental reads for flows' calendar sensor (ADR-0050): what changed since a sync
token, normalised. Read-only: one GET of the person's primary calendar's events, paged. Split out of
`providers.py`, which re-exports `calendar_changes`.

A first read (no cursor) is bounded — one day back to sixty days ahead — and its last page carries the
first sync token. Later reads pass the token (`syncToken`, with `showDeleted` so a cancellation is a
change); Google answers 410 when a token is too old, which is `resync_required`, never an error.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlsplit

import httpx

from .faults import UpstreamFault
from .provider_base import ProviderError, _NO_ANSWER, _bearer, _fault_for_status

EVENTS = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
#: Pages one read follows, and events it returns at most.
PAGES_MAX = 10
PAGE_SIZE = 250
FIRST_BACK = timedelta(days=1)
FIRST_AHEAD = timedelta(days=60)
#: Where a meeting link may point. Anything else is not a meeting link.
VIDEO_HOSTS = ("meet.google.com", "zoom.us", "teams.microsoft.com", "teams.live.com", "meet.jit.si")
_URL = re.compile(r"https://[^\s<>\"')\]]{4,500}")


def video_link(e: dict) -> Optional[str]:
    """The event's meeting link: Meet's own (`hangoutLink`, a conference entry point), else the first
    https link in location/description whose host is a meeting host. https only, no credentials."""
    # the conference's own video entry point first (a Zoom/Teams add-on puts its link ONLY there),
    # then Meet's hangoutLink, then a meeting URL written into location or description
    candidates = []
    for ep in ((e.get("conferenceData") or {}).get("entryPoints") or []):
        if isinstance(ep, dict) and ep.get("entryPointType") == "video":
            candidates.append(ep.get("uri"))
    candidates.append(e.get("hangoutLink"))
    for text in (e.get("location"), e.get("description")):
        candidates.extend(_URL.findall(str(text or ""))[:20])
    for url in candidates:
        if not isinstance(url, str):
            continue
        try:
            parts = urlsplit(url)
        except ValueError:
            continue
        host = (parts.hostname or "").lower()
        if parts.scheme == "https" and not parts.username and not parts.password and any(
                host == h or host.endswith("." + h) for h in VIDEO_HOSTS):
            return url[:500]
    return None


def _when(t: Optional[dict]) -> Optional[str]:
    if not isinstance(t, dict):
        return None
    return t.get("dateTime") or t.get("date")


def normalise(e: dict) -> dict:
    """One event as `CalendarChange` (credential-broker.v1). Titles are untrusted content."""
    status = e.get("status") if e.get("status") in ("confirmed", "tentative", "cancelled") else "confirmed"
    me = next((a for a in (e.get("attendees") or []) if isinstance(a, dict) and a.get("self")), None)
    organizer_self = bool((e.get("organizer") or {}).get("self"))
    if me is not None:
        response = me.get("responseStatus") if me.get("responseStatus") in (
            "accepted", "declined", "tentative", "needsAction") else "none"
    else:
        response = "organizer" if organizer_self or not e.get("attendees") else "none"
    out = {"event_id": str(e.get("id") or "")[:1024], "status": status,
           "etag": str(e.get("etag") or "")[:200], "updated": str(e.get("updated") or "")[:40],
           "self_response": response, "organizer_self": organizer_self, "calendar_id": "primary"}
    if status != "cancelled":
        out.update(title=str(e.get("summary") or "")[:300], start=_when(e.get("start")),
                   end=_when(e.get("end")), video_url=video_link(e))
    return out


def calendar_changes(value: dict, cursor: Optional[str], *, http=None, now: Optional[datetime] = None) -> dict:
    """``{items, cursor, resync_required}`` — what changed since ``cursor`` (or the bounded first read)."""
    if http is None:
        with httpx.Client(timeout=15, follow_redirects=False) as session:
            return calendar_changes(value, cursor, http=session, now=now)
    base = {"singleEvents": "true", "showDeleted": "true", "maxResults": PAGE_SIZE}
    if cursor:
        base["syncToken"] = cursor
    else:
        t = now or datetime.now(timezone.utc)
        base.update(timeMin=(t - FIRST_BACK).isoformat(), timeMax=(t + FIRST_AHEAD).isoformat())
    items: list = []
    page = ""
    for _ in range(PAGES_MAX):
        params = dict(base, **({"pageToken": page} if page else {}))
        try:
            response = http.get(EVENTS, params=params, headers=_bearer(value))
        except _NO_ANSWER:
            raise UpstreamFault("provider", "unreachable", "Calendar is unavailable; the provider could not be reached") from None
        except httpx.HTTPError:
            raise UpstreamFault("provider", "bad_answer", "Calendar is unavailable; the provider answered unreadably") from None
        if response.status_code == 410:
            return {"items": [], "cursor": None, "resync_required": True}
        if response.status_code in (401, 403):
            raise ProviderError("Authorization rejected; reconnect this account")
        if response.status_code != 200:
            raise _fault_for_status(response.status_code, "Calendar temporarily unavailable; retry later")
        try:
            data = json.loads(response.content)
        except ValueError:
            raise UpstreamFault("provider", "bad_answer", "Calendar answered unreadably") from None
        items.extend(normalise(e) for e in (data.get("items") or []) if isinstance(e, dict) and e.get("id"))
        if data.get("nextSyncToken"):
            return {"items": items, "cursor": data["nextSyncToken"], "resync_required": False}
        page = data.get("nextPageToken") or ""
        if not page:
            break
    # A read that ran out of pages before the sync token: keep the old cursor, the next read resumes.
    return {"items": items, "cursor": cursor, "resync_required": False}
