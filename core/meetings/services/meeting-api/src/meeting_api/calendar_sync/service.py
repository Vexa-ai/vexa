"""Pure calendar-sync logic — ICS text → PlannedEvents → planned-meeting upserts.

Offline-testable: ``parse_ics`` is a pure function over the feed text + a clock; ``sync_user``
drives the injected TranscriptStore's planned-meeting primitives (the SAME ones POST /meetings
uses, so every insert takes the per-user advisory lock and respects the unique partial index).

The load-bearing rule: **one row per calendar UID — the NEXT upcoming occurrence only.** A weekly
meeting reuses the same Meet link every occurrence; two scheduled rows on one native id would
violate the active-row unique index. Importing only the next occurrence sidesteps that entirely
(the following occurrence imports on a later sweep, after the current one completes).

Import rule (fail loud, design-spec meeting-lifecycle-v2 §v4 BUG-2): EVERY upcoming event
imports. Events with a RECOGNIZABLE meeting link (Meet/Zoom/Teams via ``collector.meeting_link``)
import armed; events WITHOUT one import as LINK-LESS planned rows (``platform='unknown'``,
no native id) so the terminal can render the honest "bot not armed — no link" state instead of
the event silently vanishing. A link appearing in a later feed sweep arms the existing row.
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


def _next_occurrence(comp, *, window_start: datetime, window_end: datetime,
                     skip: Optional[set] = None) -> Optional[datetime]:
    """The event's next occurrence inside the window — DTSTART for a one-off, RRULE-expanded
    (EXDATE-respecting) for a recurring event. ``None`` when nothing falls in the window.
    ``skip`` = occurrence datetimes claimed by RECURRENCE-ID override components — those
    instances belong to the overrides, never to the master's expansion."""
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
    exdates: set[datetime] = set(skip or ())
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


def _attendees(comp) -> list[dict]:
    """The event's human attendees — ``[{email, name?, partstat?}]`` from ATTENDEE lines.
    Rooms/resources (CUTYPE=RESOURCE|ROOM) are dropped; emails lowercase (they are the stable
    identity key for people-sets and kg person entities — NEVER an audience to contact)."""
    props = comp.get("ATTENDEE")
    out: list[dict] = []
    seen: set[str] = set()
    for a in (props if isinstance(props, list) else [props] if props is not None else []):
        params = getattr(a, "params", {}) or {}
        if str(params.get("CUTYPE", "INDIVIDUAL")).upper() in ("RESOURCE", "ROOM"):
            continue
        email = str(a).strip()
        if email.lower().startswith("mailto:"):
            email = email[7:].strip()
        email = email.lower()
        if "@" not in email or email in seen:
            continue
        seen.add(email)
        entry: dict = {"email": email}
        cn = str(params.get("CN", "")).strip()
        if cn and cn.lower() != email:
            entry["name"] = cn
        partstat = str(params.get("PARTSTAT", "")).strip()
        if partstat:
            entry["partstat"] = partstat.lower()
        out.append(entry)
    return out


def _ical_text(value: Any, redact_values: tuple[str, ...] = ()) -> str:
    """One iCalendar value/parameter as lossless-enough UTF-8 JSON text."""
    encoded = value.to_ical() if hasattr(value, "to_ical") else value
    text = encoded.decode("utf-8", errors="replace") if isinstance(encoded, bytes) else str(encoded)
    for secret in redact_values:
        if secret:
            text = text.replace(secret, "[REDACTED_FEED_URL]")
    return text


def _component_metadata(comp, *, include_components: bool = True,
                        redact_values: tuple[str, ...] = ()) -> dict:
    """JSON-safe snapshot of every property + parameter on one iCalendar component.

    Values stay in their canonical iCalendar representation so timezone identifiers, recurrence
    rules, custom X-properties, attendee parameters, and provider extensions survive without a
    provider-specific schema. Repeated properties remain ordered arrays.
    """
    properties: dict[str, list[dict]] = {}
    for raw_name, value in comp.property_items():
        name = str(raw_name).upper()
        if name in ("BEGIN", "END"):
            continue
        entry: dict = {"value": _ical_text(value, redact_values)}
        params = getattr(value, "params", None)
        if params:
            entry["parameters"] = {
                str(key).upper(): [_ical_text(item, redact_values) for item in param]
                if isinstance(param, (list, tuple)) else _ical_text(param, redact_values)
                for key, param in params.items()
            }
        properties.setdefault(name, []).append(entry)
    snapshot: dict = {"name": str(getattr(comp, "name", "")).upper(),
                      "properties": properties}
    if include_components:
        children = [_component_metadata(child, redact_values=redact_values)
                    for child in getattr(comp, "subcomponents", [])]
        if children:
            snapshot["components"] = children
    return snapshot


def parse_ics(text: str, *, now: datetime,
              horizon_days: int = DEFAULT_HORIZON_DAYS,
              lookback_s: float = DEFAULT_LOOKBACK_S,
              redact_values: tuple[str, ...] = ()) -> dict:
    """Parse an ICS feed → ``{"events": [PlannedEvent], "cancelled_uids": [uid]}``.

    PlannedEvent = ``{uid, title, scheduled_at, platform, native_meeting_id, meeting_url}`` —
    ONE per UID (the next upcoming occurrence). Events WITHOUT a recognizable meeting link
    still import — their ``platform``/``native_meeting_id``/``meeting_url`` are ``None`` and
    ``sync_user`` creates them as link-less planned rows (fail loud, never a silent skip).
    Cancelled events surface as ``cancelled_uids`` so ``sync_user`` can retire their rows."""
    from icalendar import Calendar

    window_start = now - timedelta(seconds=lookback_s)
    window_end = now + timedelta(days=horizon_days)
    events: list[dict] = []
    cancelled: list[str] = []

    # Group VEVENTs by UID FIRST: a recurring series arrives as one RRULE master plus any number
    # of RECURRENCE-ID override instances sharing the UID, in ARBITRARY feed order. The next
    # occurrence must be resolved across the WHOLE group — first-component-wins silently dropped
    # a series whenever a past override happened to precede its master in the walk (observed live:
    # the OeNB bi-weekly vanished while its siblings imported fine).
    cal = Calendar.from_ical(text)
    calendar_metadata = _component_metadata(
        cal, include_components=False, redact_values=redact_values,
    )
    groups: dict[str, list] = {}
    order: list[str] = []
    for comp in cal.walk("VEVENT"):
        uid = _event_text(comp, "UID").strip()
        if not uid:
            continue
        if uid not in groups:
            groups[uid] = []
            order.append(uid)
        groups[uid].append(comp)

    for uid in order:
        comps = groups[uid]
        master = next((c for c in comps if c.get("RECURRENCE-ID") is None), None)
        overrides = [c for c in comps if c.get("RECURRENCE-ID") is not None]
        is_cancelled = lambda c: _event_text(c, "STATUS").upper() == "CANCELLED"  # noqa: E731

        # the SERIES is cancelled only when its master is (or when there IS no master and every
        # override is) — a single cancelled occurrence must never retire the whole series row
        if (master is not None and is_cancelled(master)) or (master is None and comps and all(map(is_cancelled, comps))):
            cancelled.append(uid)
            continue

        # instances claimed by overrides never come from the master's expansion (moved OR cancelled)
        override_marks: set[datetime] = set()
        for c in overrides:
            rid = _as_utc(c.get("RECURRENCE-ID") and c.get("RECURRENCE-ID").dt)
            if rid:
                override_marks.add(rid)

        candidates: list[tuple[datetime, Any]] = []
        if master is not None:
            occ = _next_occurrence(master, window_start=window_start, window_end=window_end,
                                   skip=override_marks)
            if occ is not None:
                candidates.append((occ, master))
        for c in overrides:
            if is_cancelled(c):
                continue
            dt = _as_utc(c.get("DTSTART") and c.get("DTSTART").dt)
            if dt is not None and window_start <= dt <= window_end:
                candidates.append((dt, c))
        if not candidates:
            continue
        occurrence, comp = min(candidates, key=lambda t: t[0])

        # the joinable link: Google's conference property first, then LOCATION, then DESCRIPTION
        link = None
        for source in (_event_text(comp, "X-GOOGLE-CONFERENCE"),
                       _event_text(comp, "LOCATION"),
                       _event_text(comp, "DESCRIPTION")):
            link = find_meeting_link(source)
            if link:
                break
        # no recognizable link → import LINK-LESS (fail loud; the terminal shows "no link")
        platform, native_id, url = link if link else (None, None, None)
        event_metadata = {
            "resolved_start": occurrence.isoformat(),
            "calendar": calendar_metadata,
            "component": _component_metadata(comp, redact_values=redact_values),
        }
        if master is not None and comp is not master:
            event_metadata["series_master"] = _component_metadata(
                master, redact_values=redact_values,
            )
        events.append({
            "uid": uid,
            "title": _event_text(comp, "SUMMARY").strip() or None,
            "scheduled_at": occurrence.isoformat(),
            "platform": platform,
            "native_meeting_id": native_id,
            "meeting_url": url,
            "attendees": _attendees(comp),
            "metadata": event_metadata,
        })
    return {"events": events, "cancelled_uids": cancelled}


def _calendar_sources(data: dict) -> list[dict]:
    raw = data.get("calendar_sources")
    return [dict(source) for source in raw if isinstance(source, dict) and source.get("id")] \
        if isinstance(raw, list) else []


def _source_entry(calendar_id: str, calendar_name: Optional[str], uid: str,
                  auto_join: bool, *, bot_name: Optional[str] = None,
                  metadata: Optional[dict] = None) -> dict:
    source = {"id": calendar_id, "name": calendar_name or "Calendar", "uid": uid,
              "auto_join": bool(auto_join), "bot_name": bot_name or "Vexa"}
    if metadata:
        source["event"] = metadata
    return source


def _source_updates(sources: list[dict]) -> dict:
    primary = sources[0] if sources else None
    return {
        "calendar_sources": sources or None,
        "calendar_uid": primary.get("uid") if primary else None,
        "calendar_connection_id": primary.get("id") if primary else None,
        "calendar_name": primary.get("name") if primary else None,
        "auto_join": any(bool(source.get("auto_join", True)) for source in sources),
    }


def _replace_source(sources: list[dict], source: dict) -> list[dict]:
    replaced = False
    out = []
    for item in sources:
        if item.get("id") == source["id"]:
            out.append(source)
            replaced = True
        else:
            out.append(item)
    if not replaced:
        out.append(source)
    return out


async def sync_user(store, user_id: int, parsed: dict, *, auto_join_default: bool = True,
                    calendar_id: Optional[str] = None,
                    calendar_name: Optional[str] = None,
                    bot_name: Optional[str] = None) -> dict:
    """Upsert one user's parsed feed against their meeting rows. Returns
    ``{"created": [...], "updated": [...], "cancelled": [...], counts...}`` where each list entry
    is ``{id, native, status, when}`` for the caller to fan out as WS frames.

    Rules: a legacy row is matched by ``data.calendar_uid``; plural feeds match the tuple
    ``(calendar connection id, UID)`` carried in ``data.calendar_sources``. An INTENT-status row follows the feed
    (time/title/link moves, cancellation); a row the bot FSM owns is NEVER touched; a feed event
    colliding with a MANUALLY planned row for the same (platform, native) ADOPTS that row (stamps
    the uid) instead of duplicating it."""
    rows = await store.list_meetings(user_id)
    by_uid: dict[str, dict] = {}
    by_native: dict[tuple, dict] = {}
    # Series workspace map (prep-v3 slice a): a NEW occurrence of a known calendar UID inherits
    # the workspace of the series' newest row that carries one — recurring meetings keep their
    # room without asking. An explicit unbind writes `workspace_unbound` on the row, which the
    # newest-wins scan respects as a tombstone (inheritance stops until the user binds again).
    series_ws: dict[str, Optional[str]] = {}
    series_stamp: dict[str, str] = {}
    for row in rows:
        if row.get("shared"):
            continue  # another user's meeting mounted in — never a series/identity source here
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        sources = _calendar_sources(data)
        source = next((item for item in sources if item.get("id") == calendar_id), None) \
            if calendar_id else None
        # Existing single-feed rows predate calendar_sources. The first plural sweep adopts them
        # by UID; once stamped, every later match is scoped by connection id.
        uid = source.get("uid") if source else (
            data.get("calendar_uid") if not sources and (not calendar_id or data.get("calendar_uid")) else None
        )
        if uid and (data.get("workspace_id") or data.get("workspace_unbound")):
            stamp = str(row.get("start_time") or data.get("scheduled_at") or "")
            if uid not in series_stamp or stamp >= series_stamp[uid]:
                series_stamp[uid] = stamp
                series_ws[uid] = None if data.get("workspace_unbound") else data.get("workspace_id")
        if row.get("status") in _TERMINAL:
            continue
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
            if calendar_id:
                sources = _calendar_sources(data)
                source = _source_entry(
                    calendar_id, calendar_name, ev["uid"], auto_join_default,
                    bot_name=bot_name, metadata=ev.get("metadata"),
                )
                next_sources = _replace_source(sources, source)
                if next_sources != sources:
                    updates.update(_source_updates(next_sources))
            if (data.get("title") or None) != ev["title"] and ev["title"]:
                updates["title"] = ev["title"]
            if data.get("scheduled_at") != ev["scheduled_at"]:
                updates["scheduled_at"] = ev["scheduled_at"]
            # link updates only when the feed CARRIES a link — a link-less event never strips an
            # armed row's link (and never churns the row back to 'unknown' every sweep)
            if ev["platform"] and (row.get("native_meeting_id") != ev["native_meeting_id"] or row.get("platform") != ev["platform"]):
                updates["platform"] = ev["platform"]
                updates["native_meeting_id"] = ev["native_meeting_id"]
                updates["constructed_meeting_url"] = ev["meeting_url"]
            # attendees follow the feed both ways — an invite list changes right up to the call
            if (data.get("attendees") or []) != (ev.get("attendees") or []):
                updates["attendees"] = ev.get("attendees") or []
            if not updates:
                continue
            updated = await store.update_planned_meeting(user_id, row["id"], updates)
            if isinstance(updated, dict) and not updated.get("error"):
                out["updated"].append({"id": updated["id"], "native": updated.get("native_meeting_id"),
                                       "status": updated.get("status"),
                                       "when": (updated.get("data") or {}).get("scheduled_at")})
            continue

        # no row for this uid — adopt a manual plan on the same link, else create
        # (a link-less event has no native identity to adopt by — always creates)
        manual = by_native.get((ev["platform"], ev["native_meeting_id"])) if ev["native_meeting_id"] else None
        if manual is not None:
            if manual.get("status") in _INTENT:
                manual_data = manual.get("data") or {}
                sources = _calendar_sources(manual_data)
                if calendar_id:
                    source = _source_entry(
                        calendar_id, calendar_name, ev["uid"], auto_join_default,
                        bot_name=bot_name, metadata=ev.get("metadata"),
                    )
                    next_sources = _replace_source(sources, source)
                    source_patch = _source_updates(next_sources)
                    source_patch["calendar_managed"] = bool(
                        manual_data.get("calendar_managed", False)
                    )
                elif manual_data.get("calendar_uid"):
                    continue
                else:
                    source_patch = {"calendar_uid": ev["uid"]}
                adopted = await store.update_planned_meeting(user_id, manual["id"], {
                    **source_patch,
                    "scheduled_at": ev["scheduled_at"],
                })
                if isinstance(adopted, dict) and not adopted.get("error"):
                    by_uid_row = dict(adopted)
                    by_native[(ev["platform"], ev["native_meeting_id"])] = by_uid_row
                    out["updated"].append({"id": adopted["id"], "native": adopted.get("native_meeting_id"),
                                           "status": adopted.get("status"),
                                           "when": (adopted.get("data") or {}).get("scheduled_at")})
            continue  # an FSM row on that link → leave it alone; next sweep reconciles

        inherited_ws = series_ws.get(ev["uid"])  # None = no binding OR tombstoned — both mean "don't"
        created = await store.create_planned_meeting(
            user_id,
            platform=ev["platform"] or "unknown",   # link-less imports use the link-less-plan shape
            native_meeting_id=ev["native_meeting_id"],
            title=ev["title"],
            scheduled_at=ev["scheduled_at"],
            meeting_url=ev["meeting_url"],
            auto_join=auto_join_default,
            calendar_uid=ev["uid"],
            calendar_source=_source_entry(
                calendar_id, calendar_name, ev["uid"], auto_join_default,
                bot_name=bot_name, metadata=ev.get("metadata"),
            )
            if calendar_id else None,
            workspace_id=inherited_ws,
            workspace_source="series" if inherited_ws else None,
            attendees=ev.get("attendees") or None,
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
        if calendar_id:
            data = row.get("data") if isinstance(row.get("data"), dict) else {}
            sources = _calendar_sources(data)
            remaining = [source for source in sources if source.get("id") != calendar_id]
            managed = bool(data.get("calendar_managed", bool(sources)))
            if remaining or not managed:
                patch = _source_updates(remaining)
                if not remaining and not managed:
                    patch.pop("auto_join", None)
                updated = await store.update_planned_meeting(
                    user_id, row["id"], patch,
                )
                if isinstance(updated, dict) and not updated.get("error"):
                    out["updated"].append({"id": updated["id"],
                                           "native": updated.get("native_meeting_id"),
                                           "status": updated.get("status"),
                                           "when": (updated.get("data") or {}).get("scheduled_at")})
                continue
        deleted = await store.delete_planned_meeting(user_id, row["id"])
        if deleted:
            out["cancelled"].append({"id": row["id"], "native": row.get("native_meeting_id"),
                                     "status": "deleted", "when": None})

    out["counts"] = {k: len(v) for k, v in out.items() if isinstance(v, list)}
    return out
