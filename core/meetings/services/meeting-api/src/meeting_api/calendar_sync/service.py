"""Pure calendar-sync logic — ICS text → PlannedEvents → planned-meeting upserts.

Offline-testable: ``parse_ics`` is a pure function over the feed text + a clock; ``sync_user``
drives the injected TranscriptStore's planned-meeting primitives (the SAME ones POST /meetings
uses, so every insert takes the per-user advisory lock and respects the unique partial index).

The load-bearing rule: **one row per calendar UID — the NEXT upcoming occurrence only.** A weekly
meeting reuses the same Meet link every occurrence; two scheduled rows on one native id would
violate the active-row unique index. Importing only the next occurrence sidesteps that entirely
(the following occurrence imports on a later sweep, after the current one completes).

Import filter: only events with a RECOGNIZABLE meeting link (Meet/Zoom/Teams via
``collector.meeting_link``) become meetings — a dentist appointment is not a joinable meeting.
``STATUS:CANCELLED`` events and UIDs that vanish from the feed cancel their still-planned row;
a row the bot FSM owns (live/completed) is NEVER touched by sync.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional

from ..collector.meeting_link import find_meeting_link

# How far ahead the importer looks (events beyond it import on a later sweep) and how far back a
# started occurrence still counts as "next" (keeps a due row alive while the auto-join grace runs).
DEFAULT_HORIZON_DAYS = 14
DEFAULT_LOOKBACK_S = 900

_INTENT = ("idle", "scheduled")
_TERMINAL = ("completed", "failed")


def _as_utc(value: Any) -> Optional[datetime]:
    """An icalendar DTSTART (datetime or all-day date) → tz-aware UTC datetime."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time(0, 0), tzinfo=timezone.utc)
    return None


def _event_text(comp, key: str) -> str:
    v = comp.get(key)
    return str(v) if v is not None else ""


def _next_occurrence(comp, *, window_start: datetime, window_end: datetime) -> Optional[datetime]:
    """The event's next occurrence inside the window — DTSTART for a one-off, RRULE-expanded
    (EXDATE-respecting) for a recurring event. ``None`` when nothing falls in the window."""
    dtstart = _as_utc(comp.get("DTSTART") and comp.get("DTSTART").dt)
    if dtstart is None:
        return None
    rrule_prop = comp.get("RRULE")
    if not rrule_prop:
        return dtstart if window_start <= dtstart <= window_end else None

    from dateutil.rrule import rrulestr

    try:
        rule = rrulestr(rrule_prop.to_ical().decode(), dtstart=dtstart)
    except (ValueError, TypeError):
        return None
    exdates: set[datetime] = set()
    ex_prop = comp.get("EXDATE")
    for ex in (ex_prop if isinstance(ex_prop, list) else [ex_prop] if ex_prop else []):
        for d in getattr(ex, "dts", []):
            ex_utc = _as_utc(d.dt)
            if ex_utc:
                exdates.add(ex_utc)
    occurrence = rule.after(window_start, inc=True)
    while occurrence is not None:
        occ_utc = _as_utc(occurrence)
        if occ_utc is None or occ_utc > window_end:
            return None
        if occ_utc not in exdates:
            return occ_utc
        occurrence = rule.after(occurrence)
    return None


def parse_ics(text: str, *, now: datetime,
              horizon_days: int = DEFAULT_HORIZON_DAYS,
              lookback_s: float = DEFAULT_LOOKBACK_S) -> dict:
    """Parse an ICS feed → ``{"events": [PlannedEvent], "cancelled_uids": [uid]}``.

    PlannedEvent = ``{uid, title, scheduled_at, platform, native_meeting_id, meeting_url}`` —
    ONE per UID (the next upcoming occurrence), only for events carrying a recognizable meeting
    link. Cancelled events surface as ``cancelled_uids`` so ``sync_user`` can retire their rows."""
    from icalendar import Calendar

    window_start = now - timedelta(seconds=lookback_s)
    window_end = now + timedelta(days=horizon_days)
    events: list[dict] = []
    cancelled: list[str] = []
    seen: set[str] = set()

    cal = Calendar.from_ical(text)
    for comp in cal.walk("VEVENT"):
        uid = _event_text(comp, "UID").strip()
        if not uid or uid in seen:
            continue
        seen.add(uid)
        if _event_text(comp, "STATUS").upper() == "CANCELLED":
            cancelled.append(uid)
            continue
        occurrence = _next_occurrence(comp, window_start=window_start, window_end=window_end)
        if occurrence is None:
            continue
        # the joinable link: Google's conference property first, then LOCATION, then DESCRIPTION
        link = None
        for source in (_event_text(comp, "X-GOOGLE-CONFERENCE"),
                       _event_text(comp, "LOCATION"),
                       _event_text(comp, "DESCRIPTION")):
            link = find_meeting_link(source)
            if link:
                break
        if not link:
            continue  # no recognizable meeting link → not a joinable meeting, skip
        platform, native_id, url = link
        events.append({
            "uid": uid,
            "title": _event_text(comp, "SUMMARY").strip() or None,
            "scheduled_at": occurrence.isoformat(),
            "platform": platform,
            "native_meeting_id": native_id,
            "meeting_url": url,
        })
    return {"events": events, "cancelled_uids": cancelled}


async def sync_user(store, user_id: int, parsed: dict, *, auto_join_default: bool = True) -> dict:
    """Upsert one user's parsed feed against their meeting rows. Returns
    ``{"created": [...], "updated": [...], "cancelled": [...], counts...}`` where each list entry
    is ``{id, native, status, when}`` for the caller to fan out as WS frames.

    Rules: a row is matched by ``data.calendar_uid``; an INTENT-status row follows the feed
    (time/title/link moves, cancellation); a row the bot FSM owns is NEVER touched; a feed event
    colliding with a MANUALLY planned row for the same (platform, native) ADOPTS that row (stamps
    the uid) instead of duplicating it."""
    rows = await store.list_meetings(user_id)
    by_uid: dict[str, dict] = {}
    by_native: dict[tuple, dict] = {}
    for row in rows:
        if row.get("status") in _TERMINAL or row.get("shared"):
            continue
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        uid = data.get("calendar_uid")
        if uid and uid not in by_uid:
            by_uid[uid] = row
        if row.get("native_meeting_id"):
            by_native.setdefault((row["platform"], row["native_meeting_id"]), row)

    out = {"created": [], "updated": [], "cancelled": []}

    for ev in parsed.get("events", []):
        row = by_uid.pop(ev["uid"], None)
        if row is not None:
            if row.get("status") not in _INTENT:
                continue  # the FSM owns it now — sync never fights a live/finished meeting
            data = row.get("data") if isinstance(row.get("data"), dict) else {}
            updates: dict = {}
            if (data.get("title") or None) != ev["title"] and ev["title"]:
                updates["title"] = ev["title"]
            if data.get("scheduled_at") != ev["scheduled_at"]:
                updates["scheduled_at"] = ev["scheduled_at"]
            if row.get("native_meeting_id") != ev["native_meeting_id"] or row.get("platform") != ev["platform"]:
                updates["platform"] = ev["platform"]
                updates["native_meeting_id"] = ev["native_meeting_id"]
                updates["constructed_meeting_url"] = ev["meeting_url"]
            if not updates:
                continue
            updated = await store.update_planned_meeting(user_id, row["id"], updates)
            if isinstance(updated, dict) and not updated.get("error"):
                out["updated"].append({"id": updated["id"], "native": updated.get("native_meeting_id"),
                                       "status": updated.get("status"),
                                       "when": (updated.get("data") or {}).get("scheduled_at")})
            continue

        # no row for this uid — adopt a manual plan on the same link, else create
        manual = by_native.get((ev["platform"], ev["native_meeting_id"]))
        if manual is not None:
            if manual.get("status") in _INTENT and not (manual.get("data") or {}).get("calendar_uid"):
                adopted = await store.update_planned_meeting(user_id, manual["id"], {
                    "calendar_uid": ev["uid"],
                    "scheduled_at": ev["scheduled_at"],
                })
                if isinstance(adopted, dict) and not adopted.get("error"):
                    by_uid_row = dict(adopted)
                    by_native[(ev["platform"], ev["native_meeting_id"])] = by_uid_row
                    out["updated"].append({"id": adopted["id"], "native": adopted.get("native_meeting_id"),
                                           "status": adopted.get("status"),
                                           "when": (adopted.get("data") or {}).get("scheduled_at")})
            continue  # an FSM row on that link → leave it alone; next sweep reconciles

        created = await store.create_planned_meeting(
            user_id,
            platform=ev["platform"],
            native_meeting_id=ev["native_meeting_id"],
            title=ev["title"],
            scheduled_at=ev["scheduled_at"],
            meeting_url=ev["meeting_url"],
            auto_join=auto_join_default,
            calendar_uid=ev["uid"],
        )
        if isinstance(created, dict) and not created.get("error"):
            by_native[(ev["platform"], ev["native_meeting_id"])] = created
            out["created"].append({"id": created["id"], "native": created.get("native_meeting_id"),
                                   "status": created.get("status"),
                                   "when": (created.get("data") or {}).get("scheduled_at")})

    # UIDs cancelled in the feed, or gone from it entirely — retire their STILL-PLANNED rows.
    cancelled_uids = set(parsed.get("cancelled_uids", [])) | set(by_uid.keys())
    for uid in cancelled_uids:
        row = by_uid.get(uid)
        if row is None:
            # explicitly-cancelled uid whose row was already consumed above (or never existed)
            continue
        if row.get("status") not in _INTENT:
            continue
        deleted = await store.delete_planned_meeting(user_id, row["id"])
        if deleted:
            out["cancelled"].append({"id": row["id"], "native": row.get("native_meeting_id"),
                                     "status": "deleted", "when": None})

    out["counts"] = {k: len(v) for k, v in out.items() if isinstance(v, list)}
    return out
