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

Adoption by link is by (platform, native) over EVERY non-terminal row, intent AND live. An
imported event whose meeting is already running attaches its calendar identity to that live row
(``_attach_live_source`` — uid/sources only, never ``auto_join``/``scheduled_at``/status) rather
than importing a sibling: a sibling is what the auto-join sweep sends a SECOND bot for.

TERMINAL rows are past and are never matched or adopted — but they are not weightless, and WHICH
terminal a row reached decides opposite things about whether its occurrence still wants a bot. That
decision is not made here: ``lifecycle.occurrence.disposition`` owns the whole table (USER_STOPPED /
SERVED / RETRY over every status × completion_reason the codebase can emit), and this module asks it.

A **RETRY** occurrence is unserved — the bot never reached the room. When the auto-join sweep
dispatched for one (``data.auto_join_last_attempt``) and its bot then failed to join, the row goes
terminal, leaves this module's index, and the very next sweep of the same feed finds an unmatched UID
and creates a sibling that is due on sight — a fresh bot every sync interval until the grace window
closes (live 2026-08-17, user 13820). So the replacement row CARRIES that spent attempt forward
(``_spent_attempt``), scoped to the OCCURRENCE and not the UID: a weekly meeting whose bot failed
today still sends one tomorrow.

A **SERVED** or **USER_STOPPED** occurrence is finished, and recreating it hands the sweep a row to
send a second bot on. That is exactly what happened on stage rev 192: meeting 26312 was deliberately
stopped at 22:28:31, sync recreated the occurrence as 26313 at 22:28:46, and the sweep — its 300s
backoff since 22:21:01 legitimately elapsed — dispatched a fresh bot at 22:28:47, into the founder's
meeting, uninvited. So a finished occurrence is NOT recreated (``_finished_row``): it is already in
the user's list as the terminal meeting that carries its transcript, which is a truer record than an
empty planned sibling, and no row means nothing for the sweep to arm. Occurrence-scoped like the
backoff (``OCCURRENCE_MATCH_S``), so next week's instance imports armed.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional

from ..collector.meeting_link import find_meeting_link
from ..lifecycle import may_dispatch_again

# How far ahead the importer looks (events beyond it import on a later sweep) and how far back a
# started occurrence still counts as "next" (keeps a due row alive while the auto-join grace runs).
DEFAULT_HORIZON_DAYS = 14
DEFAULT_LOOKBACK_S = 900

# How close two ``scheduled_at`` values must be to be the SAME occurrence of a UID. A feed nudging
# a start by a few seconds between sweeps is the same occurrence; the next occurrence of any real
# recurrence is minutes-to-days away, never inside this. Deliberately far below the shortest
# plausible recurrence interval — the suppression this scopes must NEVER reach tomorrow's meeting.
OCCURRENCE_MATCH_S = 300

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

    ``recursive=False`` is load-bearing: icalendar's ``property_items`` defaults to walking the
    component's DESCENDANTS as well, so the flat property map for one VEVENT absorbed every OTHER
    VEVENT's UID/SUMMARY/DESCRIPTION/ATTENDEE/LOCATION when called on the Calendar, and every
    VALARM's when called on an event (measured live 2026-08-17: four UIDs inside one event's
    snapshot — a cross-event property bleed AND O(N²) stored bytes). Child components still ride
    along, in their own ``components`` entries, via the explicit ``subcomponents`` walk below.
    """
    properties: dict[str, list[dict]] = {}
    for raw_name, value in comp.property_items(recursive=False):
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
    # one customer's bi-weekly vanished while its siblings imported fine).
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


def _auto_join_user_set(data: dict) -> bool:
    """Has the USER pinned this row's auto-join with a PATCH? Their choice outranks the feed."""
    return bool(data.get("auto_join_user_set"))


def _source_updates(sources: list[dict], *, user_set: bool = False) -> dict:
    """The row patch that mirrors ``sources`` — the plural authority plus the singular fields
    old clients still read. ``auto_join`` is DERIVED from the connected calendars' policy, so it
    is omitted once the user has set it per-meeting (``user_set``): a sweep must never re-arm a
    meeting the user disarmed."""
    primary = sources[0] if sources else None
    updates = {
        "calendar_sources": sources or None,
        "calendar_uid": primary.get("uid") if primary else None,
        "calendar_connection_id": primary.get("id") if primary else None,
        "calendar_name": primary.get("name") if primary else None,
    }
    if not user_set:
        updates["auto_join"] = any(
            bool(source.get("auto_join", True)) for source in sources
        )
    return updates


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


async def _attach_live_source(store, user_id: int, row: dict, ev: dict, *,
                              calendar_id: Optional[str], calendar_name: Optional[str],
                              bot_name: Optional[str], auto_join_default: bool):
    """Attach this calendar's source to a row the bot FSM already owns — IDENTITY ONLY.

    An event whose meeting is ALREADY LIVE (the user sent a bot by hand, or an earlier occurrence
    is still running) must not import as a sibling row: the auto-join sweep would then dispatch a
    SECOND bot into the same room at the scheduled start (observed live 2026-08-17 — rows
    26237 live + 26251 imported, one native ``mjm-dycn-qdp``, two bots).

    Only the calendar identity keys are merged (``calendar_uid`` + ``calendar_sources`` and the
    singular mirrors). ``auto_join``, ``auto_join_user_set``, ``calendar_managed``, ``scheduled_at``
    and ``status`` are NEVER written here: adopting a live row must never re-arm it, and the row's
    auto-join marker belongs to the user's own PATCH.
    """
    attach = getattr(store, "attach_calendar_source", None)
    if attach is None:
        return None
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    sources: Optional[list[dict]]
    if calendar_id:
        source = _source_entry(
            calendar_id, calendar_name, ev["uid"], auto_join_default,
            bot_name=bot_name, metadata=ev.get("metadata"),
        )
        existing = _calendar_sources(data)
        sources = _replace_source(existing, source)
        if sources == existing and data.get("calendar_uid") == ev["uid"]:
            return None
    else:
        # legacy singular feed: never steal a row another UID already stamped
        if data.get("calendar_uid"):
            return None
        sources = None
    return await attach(user_id, row["id"], calendar_uid=ev["uid"], calendar_sources=sources)


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _same_occurrence(left: Any, right: Any) -> bool:
    """Do two ISO ``scheduled_at`` values name the same occurrence (within OCCURRENCE_MATCH_S)?"""
    a, b = _as_utc(_parse_iso(left)), _as_utc(_parse_iso(right))
    if a is None or b is None:
        return False
    return abs((a - b).total_seconds()) <= OCCURRENCE_MATCH_S


def _finished_row(row: Optional[dict], ev: dict) -> Optional[dict]:
    """The terminal row that already FINISHED this event's occurrence, or ``None``.

    Finished means ``lifecycle.occurrence`` classed the row SERVED (the bot reached the room and the
    visit is over) or USER_STOPPED (the user said no). Either way the occurrence must not be
    recreated: the recreated row is what the auto-join sweep dispatches a SECOND bot on once the
    backoff elapses (stage rev 192: 26312 completed/stopped 22:28:31 → 26313 recreated 22:28:46 →
    bot dispatched 22:28:47, uninvited, into a meeting the founder had ended).

    Deliberately NOT gated on ``auto_join_last_attempt`` the way ``_spent_attempt`` is. The backoff
    needs positive evidence of a dispatch because it is a rate limit on retries; this is not a retry
    rule but an eligibility rule, and an occurrence the user botted BY HAND and then stopped is as
    finished as one the sweep dispatched.

    Occurrence-scoped (``OCCURRENCE_MATCH_S``), never UID-scoped: today's weekly standup being
    finished says nothing about next week's, which imports armed.
    """
    if not isinstance(row, dict) or may_dispatch_again(row):
        return None
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    if not _same_occurrence(data.get("scheduled_at"), ev.get("scheduled_at")):
        return None
    return row


def _spent_attempt(row: dict, ev: dict) -> Optional[dict]:
    """The auto-join backoff a FAILED row leaves owed to a re-import of the SAME occurrence.

    ``None`` when nothing is owed. A ``failed`` row that carries ``auto_join_last_attempt`` is a row
    the auto-join sweep dispatched a bot for; if the bot then failed to join, the row went terminal
    and dropped out of both the sweep's predicate and this module's ``by_uid`` index — so the very
    next sync saw an unmatched UID and created a sibling, which was due on sight. The result was a
    fresh bot every sync interval until the grace window expired (live 2026-08-17, user 13820:
    26265 failed 20:06:25 → 26267 created 20:06:35 → dispatched 20:06:42).

    The fix is to carry the attempt forward, not to suppress the import: the row still appears, the
    terminal still renders it, and it re-arms once the backoff the sweep owns has elapsed.

    Scoped to the OCCURRENCE, never to the UID: a weekly meeting whose bot failed today must still
    send one tomorrow. Same UID + a ``scheduled_at`` outside ``OCCURRENCE_MATCH_S`` is a different
    occurrence and owes nothing.
    """
    if not may_dispatch_again(row) or row.get("status") not in _TERMINAL:
        return None
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    attempted_at = data.get("auto_join_last_attempt")
    if not isinstance(attempted_at, str) or not attempted_at:
        return None  # positive evidence only — a row nothing auto-dispatched owes no backoff
    if not _same_occurrence(data.get("scheduled_at"), ev.get("scheduled_at")):
        return None
    error = data.get("auto_join_error")
    if not isinstance(error, str) or not error:
        # The sweep's spawn SUCCEEDED and the bot failed later, so no spawn-side error was ever
        # stamped. Say the true thing rather than nothing (P18) — this row is not firing yet, and
        # the terminal has to be able to tell the user why.
        error = (f"the previous auto-join for this occurrence ended '{row.get('status')}' "
                 f"(meeting {row.get('id')}) — waiting out the auto-join backoff before retrying")
    return {"auto_join_last_attempt": attempted_at, "auto_join_error": error}


def _remember(rows: list, row: dict) -> None:
    """Fold a created/updated row back into the caller's row list — the sweep reads a user's rows
    ONCE per tick and drives every one of their connections off that one list."""
    for index, existing in enumerate(rows):
        if existing.get("id") == row.get("id"):
            rows[index] = row
            return
    rows.append(row)


def _forget(rows: list, meeting_id) -> None:
    rows[:] = [row for row in rows if row.get("id") != meeting_id]


async def sync_user(store, user_id: int, parsed: dict, *, auto_join_default: bool = True,
                    calendar_id: Optional[str] = None,
                    calendar_name: Optional[str] = None,
                    bot_name: Optional[str] = None,
                    legacy: bool = False,
                    rows: Optional[list] = None) -> dict:
    """Upsert one user's parsed feed against their meeting rows. Returns
    ``{"created": [...], "updated": [...], "cancelled": [...], counts...}`` where each list entry
    is ``{id, native, status, when}`` for the caller to fan out as WS frames.

    Rules: a legacy row is matched by ``data.calendar_uid``; plural feeds match the tuple
    ``(calendar connection id, UID)`` carried in ``data.calendar_sources``. An INTENT-status row follows the feed
    (time/title/link moves, cancellation); a row the bot FSM owns is NEVER touched; a feed event
    colliding with a MANUALLY planned row for the same (platform, native) ADOPTS that row (stamps
    the uid) instead of duplicating it.

    ``legacy`` marks the ONE connection that inherited the singular pre-plural feed: only its
    sweep claims rows carrying a bare ``calendar_uid``, so a second calendar never cancels them.
    ``rows`` (optional) lets the caller supply the user's rows — read once per tick and shared
    across that user's connections; this pass keeps the list current as it writes."""
    if rows is None:
        rows = await store.list_meetings(user_id)
    by_uid: dict[str, dict] = {}
    by_native: dict[tuple, dict] = {}
    # FAILED rows the auto-join sweep already dispatched for, newest attempt per UID. Not an
    # adoption index — a terminal row is never adopted — but the carrier of the retry backoff a
    # spent attempt owes, so recreating the row cannot re-arm it instantly (see _spent_attempt).
    terminal_attempts: dict[str, dict] = {}
    # FINISHED rows per UID (served, or user-stopped), newest occurrence wins. A finished
    # occurrence is never recreated as a dispatchable row (see _finished_row). Kept separate from
    # the backoff index because the two dispositions decide opposite things.
    finished_rows: dict[str, dict] = {}
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
        # Existing single-feed rows predate calendar_sources; the LEGACY connection's sweep (the
        # one that inherited the singular feed) adopts them by UID and stamps them. Every other
        # connection ignores them — an unstamped row is not evidence that THIS calendar dropped
        # the event. Once stamped, every later match is scoped by connection id.
        uid = source.get("uid") if source else (
            data.get("calendar_uid") if not sources and (not calendar_id or legacy) else None
        )
        if uid and (data.get("workspace_id") or data.get("workspace_unbound")):
            stamp = str(row.get("start_time") or data.get("scheduled_at") or "")
            if uid not in series_stamp or stamp >= series_stamp[uid]:
                series_stamp[uid] = stamp
                series_ws[uid] = None if data.get("workspace_unbound") else data.get("workspace_id")
        if row.get("status") in _TERMINAL:
            # A terminal row is past — it never matches, adopts, or gets touched. What it leaves
            # behind depends on how the run ENDED, and the two answers are opposites.
            if uid and not may_dispatch_again(row):
                # FINISHED (served, or the user stopped it). This occurrence must never be
                # recreated as a dispatchable row. Keep the NEWEST occurrence per UID; the
                # occurrence match happens where the event is known, so a stale week's finished
                # run never suppresses this week's.
                held = finished_rows.get(uid)
                previous = _as_utc(_parse_iso((held.get("data") or {}).get("scheduled_at"))) \
                    if held else None
                current = _as_utc(_parse_iso(data.get("scheduled_at")))
                if previous is None or (current is not None and previous <= current):
                    finished_rows[uid] = row
            elif uid:
                # UNSERVED failure: a retry is owed, but if the sweep dispatched for it, the
                # backoff that dispatch earned is owed too, and the row about to replace it is the
                # only thing left to carry it. Keep the NEWEST attempt per UID.
                attempted_at = _as_utc(_parse_iso(data.get("auto_join_last_attempt")))
                if attempted_at is not None:
                    held = terminal_attempts.get(uid)
                    previous = _as_utc(_parse_iso(
                        (held.get("data") or {}).get("auto_join_last_attempt")
                    )) if held else None
                    if previous is None or previous <= attempted_at:
                        terminal_attempts[uid] = row
            continue
        if uid and uid not in by_uid:
            by_uid[uid] = row
        # The adoption index admits EVERY non-terminal row — intent (idle/scheduled) AND the FSM
        # statuses (requested/joining/awaiting_admission/active/needs_help/stopping). A live row is
        # still the meeting on that link, so an imported event attaches to it (identity only)
        # instead of creating a sibling; only completed/failed rows are past (their link may be
        # re-met, which is what the DB's partial unique index allows).
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
                    updates.update(_source_updates(
                        next_sources, user_set=_auto_join_user_set(data),
                    ))
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
                _remember(rows, updated)
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
                    source_patch = _source_updates(
                        next_sources, user_set=_auto_join_user_set(manual_data),
                    )
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
                    _remember(rows, adopted)
                    out["updated"].append({"id": adopted["id"], "native": adopted.get("native_meeting_id"),
                                           "status": adopted.get("status"),
                                           "when": (adopted.get("data") or {}).get("scheduled_at")})
            else:
                # an FSM row on that link (live/joining/stopping) — it IS this event's meeting.
                # Stamp the calendar identity onto it so the terminal shows the link and the next
                # sweep matches it by UID; never create a sibling the auto-join sweep would send a
                # SECOND bot for, and never touch status / auto_join / scheduled_at.
                attached = await _attach_live_source(
                    store, user_id, manual, ev,
                    calendar_id=calendar_id, calendar_name=calendar_name,
                    bot_name=bot_name, auto_join_default=auto_join_default,
                )
                if isinstance(attached, dict) and not attached.get("error"):
                    by_native[(ev["platform"], ev["native_meeting_id"])] = attached
                    _remember(rows, attached)
                    out["updated"].append({"id": attached["id"],
                                           "native": attached.get("native_meeting_id"),
                                           "status": attached.get("status"),
                                           "when": (attached.get("data") or {}).get("scheduled_at")})
            continue  # never duplicate a row that already exists for this link

        # This occurrence is FINISHED — the bot came and the visit ended, or the user stopped it.
        # Recreating it hands the auto-join sweep something to dispatch on, which is how an
        # uninvited bot walked back into a meeting the founder had stopped 15 seconds earlier
        # (stage rev 192, 26312 → 26313). Nothing to create: the occurrence is already in the
        # user's list as the terminal meeting, carrying its transcript.
        if _finished_row(finished_rows.get(ev["uid"]), ev) is not None:
            continue

        inherited_ws = series_ws.get(ev["uid"])  # None = no binding OR tombstoned — both mean "don't"
        # A spent auto-join attempt on THIS occurrence rides onto the new row, so the sweep sees the
        # backoff it already earned instead of a virgin row that is due on sight.
        spent = _spent_attempt(terminal_attempts[ev["uid"]], ev) \
            if ev["uid"] in terminal_attempts else None
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
            auto_join_last_attempt=(spent or {}).get("auto_join_last_attempt"),
            auto_join_error=(spent or {}).get("auto_join_error"),
        )
        if isinstance(created, dict) and not created.get("error"):
            by_native[(ev["platform"], ev["native_meeting_id"])] = created
            _remember(rows, created)
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
            # A row the calendar CREATED is the calendar's to retire. Rows imported by the
            # singular pre-plural feed carry only `calendar_uid` — no sources, no marker — and
            # are managed all the same; defaulting them to manual would strip the stamp and
            # orphan the row instead of deleting it.
            managed = bool(data.get(
                "calendar_managed", bool(sources) or bool(data.get("calendar_uid")),
            ))
            if remaining or not managed:
                patch = _source_updates(
                    remaining, user_set=_auto_join_user_set(data),
                )
                if not remaining and not managed:
                    patch.pop("auto_join", None)
                updated = await store.update_planned_meeting(
                    user_id, row["id"], patch,
                )
                if isinstance(updated, dict) and not updated.get("error"):
                    _remember(rows, updated)
                    out["updated"].append({"id": updated["id"],
                                           "native": updated.get("native_meeting_id"),
                                           "status": updated.get("status"),
                                           "when": (updated.get("data") or {}).get("scheduled_at")})
                continue
        deleted = await store.delete_planned_meeting(user_id, row["id"])
        if deleted:
            _forget(rows, row["id"])
            out["cancelled"].append({"id": row["id"], "native": row.get("native_meeting_id"),
                                     "status": "deleted", "when": None})

    out["counts"] = {k: len(v) for k, v in out.items() if isinstance(v, list)}
    return out
