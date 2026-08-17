"""calendar_sync — ICS feed → planned meetings (parse + upsert semantics).

Pins the load-bearing rules: one row per UID (NEXT occurrence only — the unique-index sidestep
for recurring meetings), only events with recognizable meeting links import, cancelled/vanished
UIDs retire their still-planned rows, FSM-owned rows are never touched, and a manual plan on the
same link is ADOPTED (uid stamped) instead of duplicated.

Drives the SHIPPED parse_ics/sync_user over the in-memory collector store, OFFLINE.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from meeting_api.calendar_sync import aggregate_stamps, parse_ics, sync_user
from meeting_api.collector.fakes import InMemoryTranscriptStore

USER = 7
NOW = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)


def test_plural_stamps_preserve_legacy_user_status():
    stamps = [
        {"calendar_id": "work", "last_sync": "2026-07-08T12:00:00+00:00",
         "last_error": None, "counts": {"created": 1, "updated": 2, "cancelled": 0}},
        {"calendar_id": "personal", "last_sync": "2026-07-08T12:00:01+00:00",
         "last_error": "feed unavailable", "counts": {"created": 0, "updated": 1}},
    ]

    assert aggregate_stamps(stamps) == {
        "last_sync": "2026-07-08T12:00:01+00:00",
        "last_error": "feed unavailable",
        "counts": {"created": 1, "updated": 3, "cancelled": 0},
        "calendars": stamps,
    }


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
    assert {key: ev[key] for key in (
        "uid", "title", "scheduled_at", "platform", "native_meeting_id",
        "meeting_url", "attendees",
    )} == {
        "uid": "uid-1", "title": "Weekly sync",
        "scheduled_at": "2026-07-08T15:00:00+00:00",
        "platform": "google_meet", "native_meeting_id": "abc-defg-hij",
        "meeting_url": "https://meet.google.com/abc-defg-hij", "attendees": [],
    }
    assert ev["metadata"]["resolved_start"] == "2026-07-08T15:00:00+00:00"
    assert ev["metadata"]["calendar"]["properties"]["PRODID"][0]["value"] == "-//test//EN"
    assert ev["metadata"]["component"]["properties"]["UID"][0]["value"] == "uid-1"


def test_parse_preserves_all_event_properties_parameters_and_nested_components():
    event = _event(description="Agenda", status="CONFIRMED").replace(
        "END:VEVENT",
        "ORGANIZER;CN=Owner:mailto:owner@example.com\r\n"
        "DTEND:20260708T160000Z\r\n"
        "SEQUENCE:4\r\n"
        "LAST-MODIFIED:20260707T090000Z\r\n"
        "URL:https://example.com/events/uid-1\r\n"
        "CLASS:PRIVATE\r\n"
        "CATEGORIES:Customer,Review\r\n"
        "X-VEXA-CUSTOM;X-SOURCE=provider:kept exactly\r\n"
        "BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT10M\r\n"
        "DESCRIPTION:Reminder\r\nEND:VALARM\r\n"
        "END:VEVENT",
    )
    (ev,) = parse_ics(_ics(event), now=NOW)["events"]
    component = ev["metadata"]["component"]
    props = component["properties"]
    assert props["ORGANIZER"] == [{
        "value": "mailto:owner@example.com", "parameters": {"CN": "Owner"},
    }]
    assert props["DTEND"][0]["value"] == "20260708T160000Z"
    assert props["SEQUENCE"][0]["value"] == "4"
    assert props["LAST-MODIFIED"][0]["value"] == "20260707T090000Z"
    assert props["URL"][0]["value"] == "https://example.com/events/uid-1"
    assert props["CLASS"][0]["value"] == "PRIVATE"
    assert props["CATEGORIES"][0]["value"] == "Customer,Review"
    assert props["X-VEXA-CUSTOM"] == [{
        "value": "kept exactly", "parameters": {"X-SOURCE": "provider"},
    }]
    assert component["components"][0]["name"] == "VALARM"
    assert component["components"][0]["properties"]["TRIGGER"][0]["value"] == "-PT10M"


def test_parse_redacts_connected_feed_secret_from_preserved_metadata():
    secret = "https://calendar.example/private-token/basic.ics"
    event = _event().replace("END:VEVENT", f"SOURCE:{secret}\r\nEND:VEVENT")
    (ev,) = parse_ics(_ics(event), now=NOW, redact_values=(secret,))["events"]
    source = ev["metadata"]["component"]["properties"]["SOURCE"][0]["value"]
    assert source == "[REDACTED_FEED_URL]"
    assert secret not in str(ev["metadata"])


def test_parse_link_found_in_description():
    parsed = parse_ics(_ics(_event(
        location="Conference room 4",
        description="Agenda…\\nJoin: https://us02web.zoom.us/j/1234567890?pwd=x",
    )), now=NOW)
    (ev,) = parsed["events"]
    assert ev["platform"] == "zoom" and ev["native_meeting_id"] == "1234567890"


def test_parse_imports_events_without_meeting_link_as_linkless():
    # fail loud (v4 BUG-2): a link-less event is NOT silently dropped — it imports link-less
    parsed = parse_ics(_ics(_event(location="Dentist, Main St 4", description="checkup")), now=NOW)
    (ev,) = parsed["events"]
    assert ev["uid"] == "uid-1" and ev["title"] == "Weekly sync"
    assert ev["platform"] is None and ev["native_meeting_id"] is None and ev["meeting_url"] is None


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


async def test_two_calendars_same_native_meeting_share_one_row_and_both_sources():
    store = InMemoryTranscriptStore()
    parsed = parse_ics(_ics(_event()), now=NOW)
    await sync_user(store, USER, parsed, calendar_id="work", calendar_name="Work",
                    auto_join_default=False)
    result = await sync_user(store, USER, parsed, calendar_id="personal",
                             calendar_name="Personal", auto_join_default=True)
    assert result["counts"] == {"created": 0, "updated": 1, "cancelled": 0}
    (row,) = await store.list_meetings(USER)
    sources = row["data"]["calendar_sources"]
    assert [{key: source[key] for key in ("id", "name", "uid", "auto_join", "bot_name")}
            for source in sources] == [
        {"id": "work", "name": "Work", "uid": "uid-1", "auto_join": False,
         "bot_name": "Vexa"},
        {"id": "personal", "name": "Personal", "uid": "uid-1", "auto_join": True,
         "bot_name": "Vexa"},
    ]
    assert all(source["event"] == parsed["events"][0]["metadata"] for source in sources)
    assert row["data"]["auto_join"] is True


async def test_same_uid_in_two_calendars_is_scoped_by_connection_id():
    store = InMemoryTranscriptStore()
    await sync_user(store, USER, parse_ics(_ics(_event()), now=NOW),
                    calendar_id="work", calendar_name="Work")
    other = parse_ics(_ics(_event(
        location="https://meet.google.com/xyz-abcd-efg",
        start="20260709T150000Z",
    )), now=NOW)
    result = await sync_user(store, USER, other,
                             calendar_id="personal", calendar_name="Personal")
    assert result["counts"]["created"] == 1
    rows = await store.list_meetings(USER)
    assert len(rows) == 2
    assert {row["data"]["calendar_connection_id"] for row in rows} == {"work", "personal"}


async def test_disconnecting_one_calendar_preserves_other_source_and_recomputes_auto_join():
    store = InMemoryTranscriptStore()
    parsed = parse_ics(_ics(_event()), now=NOW)
    await sync_user(store, USER, parsed, calendar_id="work", calendar_name="Work",
                    auto_join_default=True)
    await sync_user(store, USER, parsed, calendar_id="personal", calendar_name="Personal",
                    auto_join_default=False)
    result = await sync_user(store, USER, {"events": [], "cancelled_uids": []},
                             calendar_id="work", calendar_name="Work")
    assert result["counts"] == {"created": 0, "updated": 1, "cancelled": 0}
    (row,) = await store.list_meetings(USER)
    (source,) = row["data"]["calendar_sources"]
    assert {key: source[key] for key in ("id", "name", "uid", "auto_join", "bot_name")} == {
        "id": "personal", "name": "Personal", "uid": "uid-1", "auto_join": False,
        "bot_name": "Vexa",
    }
    assert row["data"]["calendar_name"] == "Personal"
    assert row["data"]["auto_join"] is False


async def test_disconnecting_last_calendar_removes_calendar_managed_plan():
    store = InMemoryTranscriptStore()
    await sync_user(store, USER, parse_ics(_ics(_event()), now=NOW),
                    calendar_id="work", calendar_name="Work")
    result = await sync_user(store, USER, {"events": [], "cancelled_uids": []},
                             calendar_id="work", calendar_name="Work")
    assert result["counts"]["cancelled"] == 1
    assert await store.list_meetings(USER) == []


async def test_disconnecting_calendar_does_not_delete_adopted_manual_plan():
    store = InMemoryTranscriptStore()
    manual = await store.create_planned_meeting(
        USER, platform="google_meet", native_meeting_id="abc-defg-hij",
        title="Manual", auto_join=True,
    )
    await sync_user(store, USER, parse_ics(_ics(_event()), now=NOW),
                    calendar_id="work", calendar_name="Work")
    await sync_user(store, USER, {"events": [], "cancelled_uids": []},
                    calendar_id="work", calendar_name="Work")
    (row,) = await store.list_meetings(USER)
    assert row["id"] == manual["id"]
    assert row["data"]["title"] == "Manual"
    assert "calendar_sources" not in row["data"]

# ---- link-less imports (fail loud, v4 BUG-2) -----------------------------------------

async def test_sync_creates_linkless_planned_row():
    store = InMemoryTranscriptStore()
    parsed = parse_ics(_ics(_event(location="Room 4", description="no link here")), now=NOW)
    result = await sync_user(store, USER, parsed)
    assert result["counts"] == {"created": 1, "updated": 0, "cancelled": 0}
    (row,) = await store.list_meetings(USER)
    assert row["platform"] == "unknown" and row["native_meeting_id"] is None
    assert row["status"] == "scheduled"
    assert row["data"]["calendar_uid"] == "uid-1"
    assert row["data"]["title"] == "Weekly sync"


async def test_sync_linkless_import_is_idempotent_and_never_strips_a_link():
    store = InMemoryTranscriptStore()
    linkless = parse_ics(_ics(_event(location="Room 4")), now=NOW)
    await sync_user(store, USER, linkless)
    # idempotent re-sweep of the same link-less feed: no churn
    again = await sync_user(store, USER, linkless)
    assert again["counts"] == {"created": 0, "updated": 0, "cancelled": 0}
    # a later sweep where the event GAINS a link arms the SAME row
    armed = parse_ics(_ics(_event()), now=NOW)
    result = await sync_user(store, USER, armed)
    assert result["counts"]["updated"] == 1
    (row,) = await store.list_meetings(USER)
    assert row["platform"] == "google_meet" and row["native_meeting_id"] == "abc-defg-hij"
    # and the reverse: the feed dropping the link does NOT strip the armed row
    stripped = await sync_user(store, USER, linkless)
    assert stripped["counts"] == {"created": 0, "updated": 0, "cancelled": 0}
    (row,) = await store.list_meetings(USER)
    assert row["native_meeting_id"] == "abc-defg-hij"


async def test_sync_two_linkless_events_create_two_rows():
    store = InMemoryTranscriptStore()
    parsed = parse_ics(_ics(
        _event(uid="u-a", summary="Standup", location="Office"),
        _event(uid="u-b", summary="1:1", location="Cafe", start="20260709T090000Z"),
    ), now=NOW)
    result = await sync_user(store, USER, parsed)
    assert result["counts"]["created"] == 2
    rows = await store.list_meetings(USER)
    assert sorted((r["data"]["calendar_uid"] for r in rows)) == ["u-a", "u-b"]


async def test_sync_vanished_linkless_uid_retires_row():
    store = InMemoryTranscriptStore()
    await sync_user(store, USER, parse_ics(_ics(_event(location="Room 4")), now=NOW))
    result = await sync_user(store, USER, parse_ics(_ics(), now=NOW))
    assert result["counts"]["cancelled"] == 1
    assert await store.list_meetings(USER) == []


# ---- attendees + series workspace inheritance (prep-v3 slices a+b) -------------------

def _attendee_lines() -> str:
    return ("ATTENDEE;CN=Marvin Hanke;PARTSTAT=ACCEPTED:mailto:marvin.hanke@oenb.at\r\n"
            "ATTENDEE;CN=Roland Ramp:mailto:Roland.Ramp@oenb.at\r\n"
            "ATTENDEE;CUTYPE=ROOM;CN=Vienna 4F:mailto:room4f@oenb.at\r\n"
            "ATTENDEE:mailto:marvin.hanke@oenb.at\r\n")


def _event_with_attendees(uid="uid-1", **kw) -> str:
    ev = _event(uid=uid, **kw)
    return ev.replace("END:VEVENT", _attendee_lines() + "END:VEVENT")


def test_parse_extracts_attendees_filtering_rooms_and_dupes():
    parsed = parse_ics(_ics(_event_with_attendees()), now=NOW)
    (ev,) = parsed["events"]
    assert ev["attendees"] == [
        {"email": "marvin.hanke@oenb.at", "name": "Marvin Hanke", "partstat": "accepted"},
        {"email": "roland.ramp@oenb.at", "name": "Roland Ramp"},
    ]


def test_parse_event_without_attendee_lines_has_empty_list():
    parsed = parse_ics(_ics(_event()), now=NOW)
    assert parsed["events"][0]["attendees"] == []


async def test_sync_stores_attendees_on_create_and_follows_feed_changes():
    store = InMemoryTranscriptStore()
    await sync_user(store, USER, parse_ics(_ics(_event_with_attendees()), now=NOW))
    (row,) = await store.list_meetings(USER)
    assert [a["email"] for a in row["data"]["attendees"]] == [
        "marvin.hanke@oenb.at", "roland.ramp@oenb.at"]
    # feed drops one attendee → the row follows (invite lists change up to the call)
    result = await sync_user(store, USER, parse_ics(_ics(
        _event(uid="uid-1").replace(
            "END:VEVENT",
            "ATTENDEE;CN=Marvin Hanke:mailto:marvin.hanke@oenb.at\r\nEND:VEVENT")), now=NOW))
    assert result["counts"]["updated"] == 1
    (row,) = await store.list_meetings(USER)
    assert [a["email"] for a in row["data"]["attendees"]] == ["marvin.hanke@oenb.at"]


async def test_sync_stores_and_updates_complete_event_metadata_per_calendar_source():
    store = InMemoryTranscriptStore()
    first = parse_ics(_ics(_event(description="First").replace(
        "END:VEVENT", "X-PROVIDER-REV:one\r\nEND:VEVENT")), now=NOW)
    await sync_user(store, USER, first, calendar_id="work", calendar_name="Work",
                    bot_name="Work Notes")
    (row,) = await store.list_meetings(USER)
    (source,) = row["data"]["calendar_sources"]
    assert source["bot_name"] == "Work Notes"
    assert source["event"] == first["events"][0]["metadata"]

    changed = parse_ics(_ics(_event(description="Second").replace(
        "END:VEVENT", "X-PROVIDER-REV:two\r\nEND:VEVENT")), now=NOW)
    result = await sync_user(store, USER, changed, calendar_id="work", calendar_name="Work",
                             bot_name="Work Notes")
    assert result["counts"]["updated"] == 1
    (row,) = await store.list_meetings(USER)
    props = row["data"]["calendar_sources"][0]["event"]["component"]["properties"]
    assert props["DESCRIPTION"][0]["value"] == "Second"
    assert props["X-PROVIDER-REV"][0]["value"] == "two"


async def test_sync_new_occurrence_inherits_series_workspace():
    store = InMemoryTranscriptStore()
    # last week's occurrence of the series ran and completed, bound to the series room
    store.seed_meeting(
        user_id=USER, platform="google_meet", native_meeting_id="abc-defg-hij",
        status="completed", start_time="2026-07-01T15:00:00Z",
        data={"calendar_uid": "uid-1", "workspace_id": "oenb-1424e3"},
    )
    result = await sync_user(store, USER, parse_ics(_ics(_event()), now=NOW))
    assert result["counts"]["created"] == 1
    new = next(r for r in await store.list_meetings(USER) if r["status"] in ("idle", "scheduled"))
    assert new["data"]["workspace_id"] == "oenb-1424e3"
    assert new["data"]["workspace_source"] == "series"


async def test_sync_inherit_respects_unbind_tombstone():
    store = InMemoryTranscriptStore()
    store.seed_meeting(
        user_id=USER, platform="google_meet", native_meeting_id="abc-defg-hij",
        status="completed", start_time="2026-07-01T15:00:00Z",
        data={"calendar_uid": "uid-1", "workspace_unbound": True},
    )
    await sync_user(store, USER, parse_ics(_ics(_event()), now=NOW))
    new = next(r for r in await store.list_meetings(USER) if r["status"] in ("idle", "scheduled"))
    assert "workspace_id" not in new["data"]


async def test_sync_inherit_newest_row_wins():
    store = InMemoryTranscriptStore()
    store.seed_meeting(
        user_id=USER, platform="google_meet", native_meeting_id="abc-defg-hij",
        status="completed", start_time="2026-06-24T15:00:00Z",
        data={"calendar_uid": "uid-1", "workspace_id": "old-room"},
    )
    # the newer occurrence was explicitly unbound — inheritance must stop
    store.seed_meeting(
        user_id=USER, platform="google_meet", native_meeting_id="abc-defg-hij",
        status="completed", start_time="2026-07-01T15:00:00Z",
        data={"calendar_uid": "uid-1", "workspace_unbound": True},
    )
    await sync_user(store, USER, parse_ics(_ics(_event()), now=NOW))
    new = next(r for r in await store.list_meetings(USER) if r["status"] in ("idle", "scheduled"))
    assert "workspace_id" not in new["data"]


async def test_unbind_writes_tombstone_and_rebind_lifts_it():
    store = InMemoryTranscriptStore()
    await sync_user(store, USER, parse_ics(_ics(_event()), now=NOW))
    (row,) = await store.list_meetings(USER)
    updated = await store.update_planned_meeting(USER, row["id"], {"workspace_id": None})
    assert updated["data"].get("workspace_unbound") is True
    rebound = await store.update_planned_meeting(USER, row["id"], {"workspace_id": "oenb-1424e3"})
    assert rebound["data"]["workspace_id"] == "oenb-1424e3"
    assert rebound["data"]["workspace_source"] == "user"
    assert "workspace_unbound" not in rebound["data"]


# ---- recurring series with RECURRENCE-ID overrides (OeNB-vanish regression) ----------

def _override(uid: str, rec_id: str, start: str, summary="Weekly sync",
              location="https://meet.google.com/abc-defg-hij", status=None) -> str:
    ev = _event(uid=uid, summary=summary, start=start, location=location, status=status)
    return ev.replace("DTSTART:", f"RECURRENCE-ID:{rec_id}\r\nDTSTART:", 1)


def test_parse_series_survives_past_overrides_walking_before_master():
    """The live OeNB bug: past RECURRENCE-ID instances precede the RRULE master in the feed —
    first-component-wins consumed the UID on a dead override and dropped the whole series."""
    parsed = parse_ics(_ics(
        _override("uid-1", "20260622T150000Z", "20260622T150000Z"),   # past instance, walks FIRST
        _override("uid-1", "20260629T150000Z", "20260629T150000Z"),   # another past instance
        _event(uid="uid-1", start="20260223T150000Z", rrule="FREQ=WEEKLY;INTERVAL=2;BYDAY=MO"),
    ), now=NOW)
    (ev,) = parsed["events"]
    assert ev["scheduled_at"] == "2026-07-13T15:00:00+00:00"  # the master's next Monday-biweekly


def test_parse_moved_override_wins_over_master_expansion():
    """An occurrence moved via RECURRENCE-ID replaces the master's instance — the override's
    NEW time imports, and the master must not re-emit the claimed slot."""
    parsed = parse_ics(_ics(
        _event(uid="uid-1", start="20260622T150000Z", rrule="FREQ=WEEKLY;BYDAY=MO"),
        _override("uid-1", "20260713T150000Z", "20260710T090000Z"),   # Jul 13 moved to Jul 10
    ), now=NOW)
    (ev,) = parsed["events"]
    assert ev["scheduled_at"] == "2026-07-10T09:00:00+00:00"
    assert ev["metadata"]["component"]["properties"]["RECURRENCE-ID"][0]["value"] == \
        "20260713T150000Z"
    assert ev["metadata"]["series_master"]["properties"]["RRULE"][0]["value"] == \
        "FREQ=WEEKLY;BYDAY=MO"


def test_parse_cancelled_override_skips_occurrence_not_series():
    """STATUS:CANCELLED on ONE instance skips that occurrence; the series lives on."""
    parsed = parse_ics(_ics(
        _event(uid="uid-1", start="20260622T150000Z", rrule="FREQ=WEEKLY;BYDAY=MO"),
        _override("uid-1", "20260713T150000Z", "20260713T150000Z", status="CANCELLED"),
    ), now=NOW)
    assert parsed["cancelled_uids"] == []
    (ev,) = parsed["events"]
    assert ev["scheduled_at"] == "2026-07-20T15:00:00+00:00"  # next non-cancelled Monday


def test_parse_cancelled_master_retires_series():
    parsed = parse_ics(_ics(
        _override("uid-1", "20260713T150000Z", "20260713T150000Z"),
        _event(uid="uid-1", start="20260622T150000Z", rrule="FREQ=WEEKLY;BYDAY=MO", status="CANCELLED"),
    ), now=NOW)
    assert parsed["cancelled_uids"] == ["uid-1"]
    assert parsed["events"] == []


# ---- the user's own choices outrank the feed --------------------------------------------

async def test_user_disarmed_meeting_stays_disarmed_across_sweeps():
    """A PATCH of `auto_join` is the user's decision about ONE meeting. The sweep derives the
    flag from the connected calendars' policy on every pass, so it must stand down on that row —
    otherwise the next tick silently re-arms a bot the user turned off."""
    store = InMemoryTranscriptStore()
    parsed = parse_ics(_ics(_event()), now=NOW)
    await sync_user(store, USER, parsed, calendar_id="work", calendar_name="Work",
                    auto_join_default=True)
    (row,) = await store.list_meetings(USER)
    await store.update_planned_meeting(USER, row["id"],
                                       {"auto_join": False, "auto_join_user_set": True})

    # a sweep that genuinely changes the sources (renamed calendar, moved event) still runs
    moved = parse_ics(_ics(_event(start="20260708T160000Z")), now=NOW)
    result = await sync_user(store, USER, moved, calendar_id="work",
                             calendar_name="Work calendar", auto_join_default=True)

    assert result["counts"]["updated"] == 1
    (row,) = await store.list_meetings(USER)
    assert row["data"]["auto_join"] is False
    assert row["data"]["calendar_name"] == "Work calendar"


# ---- rows imported before calendar_sources existed ---------------------------------------

def _legacy_row(store, uid="uid-1", native="abc-defg-hij"):
    """A row the singular pre-plural feed imported: `calendar_uid`, no sources, no marker."""
    return store.seed_meeting(
        user_id=USER, platform="google_meet", native_meeting_id=native, status="scheduled",
        data={"auto_join": True, "title": "Weekly sync",
              "scheduled_at": "2026-07-08T15:00:00+00:00", "calendar_uid": uid},
    )


async def test_legacy_row_gone_from_the_feed_is_deleted_not_stripped():
    """A row with only `calendar_uid` is calendar-managed all the same. Cancelled upstream, it
    must be deleted — stripping the stamp instead leaves an armed orphan no calendar can reach."""
    store = InMemoryTranscriptStore()
    mid = _legacy_row(store)

    result = await sync_user(store, USER, {"events": [], "cancelled_uids": []},
                             calendar_id="work", calendar_name="Work", legacy=True)

    assert result["counts"]["cancelled"] == 1
    assert [row["id"] for row in await store.list_meetings(USER)] == []
    assert mid not in store._meetings


async def test_second_calendar_never_claims_a_legacy_row():
    """Only the connection that inherited the singular feed may match a bare `calendar_uid`.
    Another calendar's sweep sees a row it never imported — and leaves it alone."""
    store = InMemoryTranscriptStore()
    mid = _legacy_row(store)

    result = await sync_user(store, USER, {"events": [], "cancelled_uids": []},
                             calendar_id="personal", calendar_name="Personal")

    assert result["counts"] == {"created": 0, "updated": 0, "cancelled": 0}
    (row,) = await store.list_meetings(USER)
    assert row["id"] == mid
    assert row["data"]["calendar_uid"] == "uid-1"
    assert row["data"]["auto_join"] is True


async def test_paused_calendar_disarms_the_rows_it_manages():
    """`enabled: false` reaches the sweep as a tombstone-shaped config, so the pass parses an
    empty feed and retires this connection's managed rows. Re-enabling re-imports next sweep."""
    from meeting_api.calendar_sync import run_user_sync

    store = InMemoryTranscriptStore()
    await sync_user(store, USER, parse_ics(_ics(_event()), now=NOW),
                    calendar_id="work", calendar_name="Work")
    assert len(await store.list_meetings(USER)) == 1

    stamp = await run_user_sync(store, {
        "user_id": USER, "calendar_id": "work", "calendar_name": "Work",
        "bot_name": "Vexa", "deleted": False, "paused": True,
    })

    assert stamp["last_error"] is None
    assert stamp["counts"]["cancelled"] == 1
    assert await store.list_meetings(USER) == []


# ---- one row read per user per tick, shared across their calendars ------------------------

async def test_shared_rows_stay_current_across_a_users_calendars():
    """The sweep reads a user's rows ONCE and threads the list through every connection, so the
    second calendar must see the first one's insert — and share the row instead of duplicating."""
    store = InMemoryTranscriptStore()
    parsed = parse_ics(_ics(_event()), now=NOW)
    rows: list = await store.list_meetings(USER)

    first = await sync_user(store, USER, parsed, calendar_id="work", calendar_name="Work",
                            rows=rows)
    second = await sync_user(store, USER, parsed, calendar_id="personal",
                             calendar_name="Personal", rows=rows)

    assert first["counts"]["created"] == 1
    assert second["counts"] == {"created": 0, "updated": 1, "cancelled": 0}
    (row,) = await store.list_meetings(USER)
    assert [source["id"] for source in row["data"]["calendar_sources"]] == ["work", "personal"]
    assert [entry["id"] for entry in rows] == [row["id"]]
