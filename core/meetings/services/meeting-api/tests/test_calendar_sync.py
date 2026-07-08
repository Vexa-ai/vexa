"""calendar_sync — ICS feed → planned meetings (parse + upsert semantics).

Pins the load-bearing rules: one row per UID (NEXT occurrence only — the unique-index sidestep
for recurring meetings), only events with recognizable meeting links import, cancelled/vanished
UIDs retire their still-planned rows, FSM-owned rows are never touched, and a manual plan on the
same link is ADOPTED (uid stamped) instead of duplicated.

Drives the SHIPPED parse_ics/sync_user over the in-memory collector store, OFFLINE.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from meeting_api.calendar_sync import parse_ics, sync_user
from meeting_api.collector.fakes import InMemoryTranscriptStore

USER = 7
NOW = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)


def _ics(*events: str) -> str:
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n" + "".join(events) + "END:VCALENDAR\r\n"


def _event(uid="uid-1", summary="Weekly sync", start="20260708T150000Z",
           location="https://meet.google.com/abc-defg-hij", rrule=None,
           status=None, description=None) -> str:
    lines = [f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20260701T000000Z\r\nDTSTART:{start}\r\n"
             f"SUMMARY:{summary}\r\n"]
    if location:
        lines.append(f"LOCATION:{location}\r\n")
    if description:
        lines.append(f"DESCRIPTION:{description}\r\n")
    if rrule:
        lines.append(f"RRULE:{rrule}\r\n")
    if status:
        lines.append(f"STATUS:{status}\r\n")
    lines.append("END:VEVENT\r\n")
    return "".join(lines)


# ---- parse_ics ----------------------------------------------------------------------

def test_parse_single_event_with_meet_link():
    parsed = parse_ics(_ics(_event()), now=NOW)
    assert parsed["cancelled_uids"] == []
    (ev,) = parsed["events"]
    assert ev == {
        "uid": "uid-1", "title": "Weekly sync",
        "scheduled_at": "2026-07-08T15:00:00+00:00",
        "platform": "google_meet", "native_meeting_id": "abc-defg-hij",
        "meeting_url": "https://meet.google.com/abc-defg-hij",
    }


def test_parse_link_found_in_description():
    parsed = parse_ics(_ics(_event(
        location="Conference room 4",
        description="Agenda…\\nJoin: https://us02web.zoom.us/j/1234567890?pwd=x",
    )), now=NOW)
    (ev,) = parsed["events"]
    assert ev["platform"] == "zoom" and ev["native_meeting_id"] == "1234567890"


def test_parse_skips_events_without_meeting_link():
    parsed = parse_ics(_ics(_event(location="Dentist, Main St 4", description="checkup")), now=NOW)
    assert parsed["events"] == []


def test_parse_weekly_rrule_imports_only_next_occurrence():
    # started weeks ago, repeats weekly — only the NEXT upcoming occurrence imports
    parsed = parse_ics(_ics(_event(start="20260610T150000Z", rrule="FREQ=WEEKLY")), now=NOW)
    (ev,) = parsed["events"]
    assert ev["scheduled_at"] == "2026-07-08T15:00:00+00:00"  # today's occurrence, not all of them
    assert len(parsed["events"]) == 1


def test_parse_cancelled_event_surfaces_as_cancellation():
    parsed = parse_ics(_ics(_event(status="CANCELLED")), now=NOW)
    assert parsed["events"] == []
    assert parsed["cancelled_uids"] == ["uid-1"]


def test_parse_event_beyond_horizon_skipped():
    parsed = parse_ics(_ics(_event(start="20261001T150000Z")), now=NOW, horizon_days=14)
    assert parsed["events"] == []


def test_parse_past_event_skipped():
    parsed = parse_ics(_ics(_event(start="20260708T100000Z")), now=NOW)  # 2h ago, lookback 15m
    assert parsed["events"] == []


# ---- sync_user ----------------------------------------------------------------------

async def test_sync_creates_planned_row_with_uid_provenance():
    store = InMemoryTranscriptStore()
    parsed = parse_ics(_ics(_event()), now=NOW)
    result = await sync_user(store, USER, parsed, auto_join_default=True)
    assert result["counts"] == {"created": 1, "updated": 0, "cancelled": 0}
    rows = await store.list_meetings(USER)
    (row,) = rows
    assert row["status"] == "scheduled"
    assert row["data"]["calendar_uid"] == "uid-1"
    assert row["data"]["title"] == "Weekly sync"
    assert row["data"]["auto_join"] is True


async def test_sync_respects_global_auto_join_off():
    store = InMemoryTranscriptStore()
    parsed = parse_ics(_ics(_event()), now=NOW)
    await sync_user(store, USER, parsed, auto_join_default=False)
    (row,) = await store.list_meetings(USER)
    assert row["data"]["auto_join"] is False


async def test_sync_is_idempotent():
    store = InMemoryTranscriptStore()
    parsed = parse_ics(_ics(_event()), now=NOW)
    await sync_user(store, USER, parsed)
    result = await sync_user(store, USER, parsed)
    assert result["counts"] == {"created": 0, "updated": 0, "cancelled": 0}
    assert len(await store.list_meetings(USER)) == 1


async def test_sync_moved_event_updates_time():
    store = InMemoryTranscriptStore()
    await sync_user(store, USER, parse_ics(_ics(_event()), now=NOW))
    moved = parse_ics(_ics(_event(start="20260708T170000Z")), now=NOW)
    result = await sync_user(store, USER, moved)
    assert result["counts"]["updated"] == 1
    (row,) = await store.list_meetings(USER)
    assert row["data"]["scheduled_at"] == "2026-07-08T17:00:00+00:00"


async def test_sync_vanished_uid_retires_planned_row():
    store = InMemoryTranscriptStore()
    await sync_user(store, USER, parse_ics(_ics(_event()), now=NOW))
    result = await sync_user(store, USER, parse_ics(_ics(), now=NOW))  # feed now empty
    assert result["counts"]["cancelled"] == 1
    assert await store.list_meetings(USER) == []


async def test_sync_cancelled_event_retires_planned_row():
    store = InMemoryTranscriptStore()
    await sync_user(store, USER, parse_ics(_ics(_event()), now=NOW))
    cancelled = parse_ics(_ics(_event(status="CANCELLED")), now=NOW)
    result = await sync_user(store, USER, cancelled)
    assert result["counts"]["cancelled"] == 1
    assert await store.list_meetings(USER) == []


async def test_sync_never_touches_fsm_rows():
    store = InMemoryTranscriptStore()
    await sync_user(store, USER, parse_ics(_ics(_event()), now=NOW))
    (row,) = await store.list_meetings(USER)
    store._meetings[row["id"]]["status"] = "active"  # the bot joined
    # the event moves AND then vanishes — the live row must survive both
    await sync_user(store, USER, parse_ics(_ics(_event(start="20260708T170000Z")), now=NOW))
    result = await sync_user(store, USER, parse_ics(_ics(), now=NOW))
    assert result["counts"]["cancelled"] == 0
    (still,) = await store.list_meetings(USER)
    assert still["status"] == "active"


async def test_sync_adopts_manual_plan_on_same_link():
    store = InMemoryTranscriptStore()
    manual = await store.create_planned_meeting(
        USER, platform="google_meet", native_meeting_id="abc-defg-hij",
        title="My prep title", meeting_url="https://meet.google.com/abc-defg-hij",
        workspace_id="ws-1",
    )
    result = await sync_user(store, USER, parse_ics(_ics(_event()), now=NOW))
    assert result["counts"] == {"created": 0, "updated": 1, "cancelled": 0}
    rows = await store.list_meetings(USER)
    (row,) = rows                                     # adopted, NOT duplicated
    assert row["id"] == manual["id"]
    assert row["data"]["calendar_uid"] == "uid-1"     # provenance stamped
    assert row["data"]["title"] == "My prep title"    # the user's title wins
    assert row["data"]["workspace_id"] == "ws-1"      # the workspace bind survives
    assert row["status"] == "scheduled"               # feed time attached


async def test_sync_other_users_rows_untouched():
    store = InMemoryTranscriptStore()
    await sync_user(store, USER, parse_ics(_ics(_event()), now=NOW))
    await sync_user(store, 99, parse_ics(_ics(), now=NOW))  # user 99's empty feed
    assert len(await store.list_meetings(USER)) == 1
