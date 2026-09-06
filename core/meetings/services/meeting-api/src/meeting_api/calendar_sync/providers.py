"""Provider event JSON → the SAME PlannedEvent shape ``parse_ics`` produces.

The ICS feed is one way to learn a user's calendar, not the concept. ``sync_user`` already owns
every hard part — one row per UID, adoption by link, occurrence disposition, retirement — and it
consumes a plain dict. So a calendar API is a new *reader*, not a new pipeline: these functions
map Google Calendar and Microsoft Graph event payloads onto ``{"events": [...], "cancelled_uids":
[...]}`` and nothing downstream can tell which reader produced them.

Pure. No network, no tokens, no clock of their own — the caller passes ``now``, exactly as
``parse_ics`` requires, so every mapping is testable offline against a fixture.

**Recurrence is the provider's job here, not ours.** ``parse_ics`` expands RRULEs itself because a
feed hands us a master plus overrides. Both APIs will expand for us when asked — Google with
``singleEvents=true``, Graph via ``/calendarView`` — so the caller MUST request expanded instances.
We then apply the same load-bearing rule ``parse_ics`` applies: **group by the series-stable id and
keep only the earliest occurrence in the window.** Two scheduled rows on one native id would
violate ``uq_meeting_active_user_platform_native``.

The series-stable id is the UID, deliberately: Google's ``iCalUID`` and Graph's ``iCalUId`` are the
same value the ICS feed would have carried for that event, so **a calendar reconnected over OAuth
adopts the rows its ICS feed created** instead of duplicating them. Instance ids
(``event['id']``, which differs per occurrence) would have re-imported the user's whole calendar.

**Snapshot policy — deliberately narrower than the ICS reader.** ``parse_ics`` copies every VEVENT
property into ``metadata.component``; that unbounded copy of arbitrary provider properties is a
known open residual (Vexa-ai/vexa#1213 item 3, "no test that the VEVENT snapshot is redacted"). A
calendar API returns far more per event than a feed does — extended properties, attachments, ACL
hints — so these readers store a BOUNDED snapshot: the fields we use, plus a short allowlist,
and nothing else. Anything not listed never enters the row.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, Optional

from ..collector.meeting_link import find_meeting_link
from .service import DEFAULT_HORIZON_DAYS, DEFAULT_LOOKBACK_S

# Per-event fields carried into ``metadata.component``. Everything else in the provider payload is
# dropped — see the snapshot-policy note above. Keep these lists SHORT and boring.
GOOGLE_SNAPSHOT_KEYS = (
    "id", "iCalUID", "status", "summary", "description", "location", "created", "updated",
    "recurringEventId", "originalStartTime", "hangoutLink", "htmlLink", "eventType",
    "transparency", "visibility", "organizer", "creator", "start", "end",
)
MICROSOFT_SNAPSHOT_KEYS = (
    "id", "iCalUId", "subject", "bodyPreview", "createdDateTime", "lastModifiedDateTime",
    "seriesMasterId", "type", "isCancelled", "isAllDay", "showAs", "sensitivity",
    "webLink", "onlineMeetingProvider", "organizer", "start", "end", "location",
)

# ICS PARTSTAT is the vocabulary every downstream consumer already reads (``_attendees`` lowercases
# it). Both providers get folded onto it so nothing downstream has to know who answered.
_GOOGLE_PARTSTAT = {
    "accepted": "accepted", "declined": "declined",
    "tentative": "tentative", "needsaction": "needs-action",
}
_MICROSOFT_PARTSTAT = {
    "accepted": "accepted", "organizer": "accepted",
    "declined": "declined",
    "tentativelyaccepted": "tentative",
    "notresponded": "needs-action", "none": "needs-action",
}


def _fold_fraction(text: str) -> str:
    """Graph emits 7-digit fractional seconds (``17:00:00.0000000``); ``fromisoformat`` wants ≤6."""
    return re.sub(r"(\.\d{6})\d+", r"\1", text)


def _iso_to_utc(value: Any, *, tz_name: Optional[str] = None) -> Optional[datetime]:
    """An ISO-8601 string → tz-aware UTC datetime, or ``None`` if it is not one.

    A value carrying its own offset wins outright. A NAIVE value is resolved against ``tz_name``
    when that names a zone we can load, and otherwise is read as **UTC** — never as the server's
    local time, which is the defect Vexa-ai/vexa#1316 and backlog R-B10 both describe. Reading it
    as UTC can be wrong by an offset; reading it as local time is wrong by wherever the pod runs,
    which changes under us and cannot be reproduced.
    """
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = _fold_fraction(value.strip())
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc)
    zone = _load_zone(tz_name)
    return parsed.replace(tzinfo=zone).astimezone(timezone.utc)


def _load_zone(tz_name: Optional[str]):
    """``tz_name`` → a tzinfo, defaulting to UTC. Windows zone ids (Graph's default vocabulary,
    e.g. "Pacific Standard Time") are not IANA and do not load; the caller is expected to send
    ``Prefer: outlook.timezone="UTC"`` so this stays the uninteresting path."""
    if not tz_name or tz_name.strip().upper() == "UTC":
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(tz_name.strip())
    except Exception:
        return timezone.utc


def _all_day_to_utc(value: Any) -> Optional[datetime]:
    """An all-day ``YYYY-MM-DD`` → midnight UTC, mirroring ``service._as_utc``'s date branch."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time(0, 0), tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.combine(date.fromisoformat(value.strip()), time(0, 0), tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _snapshot(event: dict, keys: Iterable[str]) -> dict:
    """The bounded per-event snapshot — listed keys only, in a stable order."""
    return {k: event[k] for k in keys if k in event and event[k] is not None}


def _first_link(*sources: Any):
    """The first recognizable meeting link across the given text sources, in priority order."""
    for source in sources:
        if not source:
            continue
        link = find_meeting_link(source if isinstance(source, str) else str(source))
        if link:
            return link
    return None


def _emit(uid: str, *, title: Optional[str], occurrence: datetime, link,
          attendees: list[dict], metadata: dict) -> dict:
    """One PlannedEvent, shaped exactly as ``parse_ics`` emits it."""
    platform, native_id, url = link if link else (None, None, None)
    return {
        "uid": uid,
        "title": (title or "").strip() or None,
        "scheduled_at": occurrence.isoformat(),
        "platform": platform,
        "native_meeting_id": native_id,
        "meeting_url": url,
        "attendees": attendees,
        "metadata": metadata,
    }


def _resolve(groups: dict, order: list, cancelled_only: dict) -> tuple[list, list]:
    """Group → (earliest live occurrence per uid, uids whose every in-window instance is cancelled).

    Mirrors ``parse_ics``: a single cancelled occurrence never retires the series — the uid is
    retired only when nothing live remains in the window. A uid that vanished from the payload
    entirely is not our problem; ``sync_user`` retires those on its own.
    """
    events: list[dict] = []
    cancelled: list[str] = []
    for uid in order:
        live = groups.get(uid) or []
        if live:
            events.append(min(live, key=lambda ev: ev["_occurrence"]))
        elif cancelled_only.get(uid):
            cancelled.append(uid)
    for ev in events:
        ev.pop("_occurrence", None)
    return events, cancelled


def events_from_google(items: list, *, now: datetime,
                       horizon_days: int = DEFAULT_HORIZON_DAYS,
                       lookback_s: float = DEFAULT_LOOKBACK_S,
                       calendar: Optional[dict] = None) -> dict:
    """Google Calendar ``events.list`` items → ``{"events": [...], "cancelled_uids": [...]}``.

    The caller MUST have requested ``singleEvents=true`` (so recurrences arrive expanded) and
    ``showDeleted=true`` (so a cancelled instance is visible rather than merely absent).
    """
    window_start = now - timedelta(seconds=lookback_s)
    window_end = now + timedelta(days=horizon_days)
    calendar_metadata = dict(calendar or {})
    groups: dict[str, list] = {}
    cancelled_only: dict[str, bool] = {}
    order: list[str] = []

    for item in items or []:
        if not isinstance(item, dict):
            continue
        uid = str(item.get("iCalUID") or item.get("id") or "").strip()
        if not uid:
            continue
        start = item.get("start") or {}
        occurrence = (_iso_to_utc(start.get("dateTime"), tz_name=start.get("timeZone"))
                      or _all_day_to_utc(start.get("date")))
        if occurrence is None or not (window_start <= occurrence <= window_end):
            continue
        if uid not in groups:
            groups[uid] = []
            order.append(uid)
        if str(item.get("status") or "").lower() == "cancelled":
            cancelled_only[uid] = cancelled_only.get(uid, True)
            continue
        cancelled_only[uid] = False

        link = _first_link(item.get("hangoutLink"),
                           *_google_conference_uris(item),
                           item.get("location"), item.get("description"))
        event = _emit(
            uid,
            title=item.get("summary"),
            occurrence=occurrence,
            link=link,
            attendees=_google_attendees(item.get("attendees")),
            metadata={
                "provider": "google",
                "resolved_start": occurrence.isoformat(),
                "calendar": calendar_metadata,
                "component": _snapshot(item, GOOGLE_SNAPSHOT_KEYS),
            },
        )
        event["_occurrence"] = occurrence
        groups[uid].append(event)

    events, cancelled = _resolve(groups, order, cancelled_only)
    return {"events": events, "cancelled_uids": cancelled}


def _google_conference_uris(item: dict) -> list:
    """Video entry points from ``conferenceData`` — the structured equivalent of
    X-GOOGLE-CONFERENCE, and the only place a Meet link lives for some event shapes."""
    data = item.get("conferenceData") or {}
    points = data.get("entryPoints") or []
    return [p.get("uri") for p in points
            if isinstance(p, dict) and p.get("entryPointType") in (None, "video") and p.get("uri")]


def _google_attendees(raw: Any) -> list[dict]:
    """Google attendees → ``[{email, name?, partstat?}]``, matching ``service._attendees``.

    Rooms and equipment (``resource: true``) are dropped, emails lowercased, order preserved,
    duplicates collapsed — the same contract the ICS reader promises.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for a in raw or []:
        if not isinstance(a, dict) or a.get("resource"):
            continue
        email = str(a.get("email") or "").strip().lower()
        if "@" not in email or email in seen:
            continue
        seen.add(email)
        entry: dict = {"email": email}
        name = str(a.get("displayName") or "").strip()
        if name and name.lower() != email:
            entry["name"] = name
        partstat = _GOOGLE_PARTSTAT.get(str(a.get("responseStatus") or "").strip().lower())
        if partstat:
            entry["partstat"] = partstat
        out.append(entry)
    return out


def events_from_microsoft(items: list, *, now: datetime,
                          horizon_days: int = DEFAULT_HORIZON_DAYS,
                          lookback_s: float = DEFAULT_LOOKBACK_S,
                          calendar: Optional[dict] = None) -> dict:
    """Microsoft Graph ``/calendarView`` items → the same shape.

    ``/calendarView`` (not ``/events``) is required: it expands a series into occurrences, which is
    what the one-row-per-uid rule needs. Send ``Prefer: outlook.timezone="UTC"`` so ``start.dateTime``
    arrives naive-UTC rather than in a Windows zone id we cannot resolve.
    """
    window_start = now - timedelta(seconds=lookback_s)
    window_end = now + timedelta(days=horizon_days)
    calendar_metadata = dict(calendar or {})
    groups: dict[str, list] = {}
    cancelled_only: dict[str, bool] = {}
    order: list[str] = []

    for item in items or []:
        if not isinstance(item, dict):
            continue
        uid = str(item.get("iCalUId") or item.get("iCalUid") or item.get("id") or "").strip()
        if not uid:
            continue
        start = item.get("start") or {}
        occurrence = _iso_to_utc(start.get("dateTime"), tz_name=start.get("timeZone"))
        if occurrence is None and item.get("isAllDay"):
            occurrence = _all_day_to_utc(str(start.get("dateTime") or "")[:10])
        if occurrence is None or not (window_start <= occurrence <= window_end):
            continue
        if uid not in groups:
            groups[uid] = []
            order.append(uid)
        if item.get("isCancelled") is True:
            cancelled_only[uid] = cancelled_only.get(uid, True)
            continue
        cancelled_only[uid] = False

        online = item.get("onlineMeeting") or {}
        location = item.get("location") or {}
        body = item.get("body") or {}
        link = _first_link(online.get("joinUrl"), item.get("onlineMeetingUrl"),
                           location.get("displayName"), item.get("bodyPreview"),
                           body.get("content"))
        event = _emit(
            uid,
            title=item.get("subject"),
            occurrence=occurrence,
            link=link,
            attendees=_microsoft_attendees(item.get("attendees")),
            metadata={
                "provider": "microsoft",
                "resolved_start": occurrence.isoformat(),
                "calendar": calendar_metadata,
                "component": _snapshot(item, MICROSOFT_SNAPSHOT_KEYS),
            },
        )
        event["_occurrence"] = occurrence
        groups[uid].append(event)

    events, cancelled = _resolve(groups, order, cancelled_only)
    return {"events": events, "cancelled_uids": cancelled}


def _microsoft_attendees(raw: Any) -> list[dict]:
    """Graph attendees → ``[{email, name?, partstat?}]``. ``type: "resource"`` is a room."""
    out: list[dict] = []
    seen: set[str] = set()
    for a in raw or []:
        if not isinstance(a, dict) or str(a.get("type") or "").lower() == "resource":
            continue
        address = (a.get("emailAddress") or {}) if isinstance(a.get("emailAddress"), dict) else {}
        email = str(address.get("address") or "").strip().lower()
        if "@" not in email or email in seen:
            continue
        seen.add(email)
        entry: dict = {"email": email}
        name = str(address.get("name") or "").strip()
        if name and name.lower() != email:
            entry["name"] = name
        status = (a.get("status") or {}) if isinstance(a.get("status"), dict) else {}
        partstat = _MICROSOFT_PARTSTAT.get(str(status.get("response") or "").strip().lower())
        if partstat:
            entry["partstat"] = partstat
        out.append(entry)
    return out
