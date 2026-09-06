"""calendar_sync.providers — Google/Graph event JSON → the SAME PlannedEvent shape as parse_ics.

The point of these readers is that ``sync_user`` cannot tell which one produced its input, so the
tests that matter most are the PARITY ones: same keys, same attendee vocabulary, same one-row-per-uid
rule, same "a link-less event still imports" rule. Pure and offline — no network, no tokens.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from meeting_api.calendar_sync.providers import events_from_google, events_from_microsoft
from meeting_api.calendar_sync import parse_ics, sync_user
from meeting_api.collector.fakes import InMemoryTranscriptStore

NOW = datetime(2026, 9, 6, 12, 0, 0, tzinfo=timezone.utc)
MEET = "https://meet.google.com/abc-defg-hij"
TEAMS = ("https://teams.microsoft.com/l/meetup-join/"
         "19%3ameeting_NTk0ZjExMjMtNDU2Nw%40thread.v2/0?context=%7b%22Tid%22%3a%22x%22%7d")
ZOOM = "https://zoom.us/j/1234567890"


def g_event(**over) -> dict:
    base = {
        "id": "instance-1",
        "iCalUID": "series-a@google.com",
        "status": "confirmed",
        "summary": "Weekly sync",
        "start": {"dateTime": "2026-09-06T13:00:00Z"},
        "end": {"dateTime": "2026-09-06T13:30:00Z"},
        "hangoutLink": MEET,
    }
    base.update(over)
    return base


def m_event(**over) -> dict:
    base = {
        "id": "AAMk-instance-1",
        "iCalUId": "040000008200E00074C5B7101A82E008",
        "subject": "Weekly sync",
        "start": {"dateTime": "2026-09-06T13:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2026-09-06T13:30:00.0000000", "timeZone": "UTC"},
        "onlineMeeting": {"joinUrl": TEAMS},
    }
    base.update(over)
    return base


# ---------------------------------------------------------------- shape parity with the ICS reader

ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:series-a@google.com
SUMMARY:Weekly sync
DTSTART:20260906T130000Z
DTEND:20260906T133000Z
LOCATION:https://meet.google.com/abc-defg-hij
ATTENDEE;CN=Ann;PARTSTAT=ACCEPTED:mailto:ann@example.com
END:VEVENT
END:VCALENDAR
"""


def test_every_reader_emits_the_same_planned_event_keys():
    """If these drift, sync_user starts behaving differently depending on who read the calendar."""
    ics = parse_ics(ICS, now=NOW)["events"][0]
    google = events_from_google([g_event(
        attendees=[{"email": "ann@example.com", "displayName": "Ann", "responseStatus": "accepted"}],
    )], now=NOW)["events"][0]
    microsoft = events_from_microsoft([m_event(
        attendees=[{"type": "required", "status": {"response": "accepted"},
                    "emailAddress": {"address": "ann@example.com", "name": "Ann"}}],
    )], now=NOW)["events"][0]

    assert set(google) == set(ics)
    assert set(microsoft) == set(ics)
    # and the attendee vocabulary is the ICS one, not each provider's
    assert ics["attendees"] == [{"email": "ann@example.com", "name": "Ann", "partstat": "accepted"}]
    assert google["attendees"] == ics["attendees"]
    assert microsoft["attendees"] == ics["attendees"]


def test_google_uses_the_series_uid_so_an_ics_row_is_adopted_not_duplicated():
    """iCalUID is the value the feed carried. Keying on event['id'] (per-occurrence) would
    re-import every meeting the user already had from ICS."""
    ics_uid = parse_ics(ICS, now=NOW)["events"][0]["uid"]
    assert events_from_google([g_event()], now=NOW)["events"][0]["uid"] == ics_uid


# ------------------------------------------------------------------- one row per uid, next occurrence

def test_google_keeps_only_the_earliest_in_window_occurrence_of_a_series():
    later = g_event(id="instance-3", start={"dateTime": "2026-09-20T13:00:00Z"})
    soon = g_event(id="instance-2", start={"dateTime": "2026-09-13T13:00:00Z"})
    out = events_from_google([later, soon], now=NOW)

    assert len(out["events"]) == 1
    assert out["events"][0]["scheduled_at"] == "2026-09-13T13:00:00+00:00"


def test_microsoft_keeps_only_the_earliest_in_window_occurrence_of_a_series():
    later = m_event(id="i3", start={"dateTime": "2026-09-15T09:00:00.0000000", "timeZone": "UTC"})
    soon = m_event(id="i2", start={"dateTime": "2026-09-08T09:00:00.0000000", "timeZone": "UTC"})
    out = events_from_microsoft([later, soon], now=NOW)

    assert len(out["events"]) == 1
    assert out["events"][0]["scheduled_at"] == "2026-09-08T09:00:00+00:00"


def test_events_beyond_the_horizon_are_left_for_a_later_sweep():
    assert events_from_google([g_event(start={"dateTime": "2026-10-30T13:00:00Z"})],
                              now=NOW)["events"] == []
    assert events_from_microsoft([m_event(start={"dateTime": "2026-10-30T09:00:00.0000000",
                                                 "timeZone": "UTC"})], now=NOW)["events"] == []


def test_a_started_meeting_still_imports_inside_the_lookback():
    """The auto-join grace window depends on a just-started occurrence staying visible."""
    started = g_event(start={"dateTime": "2026-09-06T11:55:00Z"})
    assert len(events_from_google([started], now=NOW)["events"]) == 1


# --------------------------------------------------------------------------------- cancellation

def test_one_cancelled_occurrence_never_retires_the_whole_series():
    cancelled = g_event(id="i2", status="cancelled",
                        start={"dateTime": "2026-09-06T13:00:00Z"})
    live = g_event(id="i3", start={"dateTime": "2026-09-13T13:00:00Z"})
    out = events_from_google([cancelled, live], now=NOW)

    assert out["cancelled_uids"] == []
    assert len(out["events"]) == 1
    assert out["events"][0]["scheduled_at"] == "2026-09-13T13:00:00+00:00"


def test_a_series_with_nothing_live_left_in_the_window_is_retired():
    out = events_from_google([g_event(status="cancelled")], now=NOW)
    assert out["events"] == []
    assert out["cancelled_uids"] == ["series-a@google.com"]


def test_microsoft_is_cancelled_retires_the_same_way():
    out = events_from_microsoft([m_event(isCancelled=True)], now=NOW)
    assert out["events"] == []
    assert out["cancelled_uids"] == ["040000008200E00074C5B7101A82E008"]


# ---------------------------------------------------------------------------------- link finding

def test_google_link_priority_conference_then_location_then_description():
    from_conference = g_event(hangoutLink=None, conferenceData={
        "entryPoints": [{"entryPointType": "phone", "uri": "tel:+1-555"},
                        {"entryPointType": "video", "uri": MEET}]})
    assert events_from_google([from_conference], now=NOW)["events"][0]["meeting_url"] == MEET

    from_location = g_event(hangoutLink=None, location=ZOOM, description=MEET)
    assert events_from_google([from_location], now=NOW)["events"][0]["meeting_url"] == ZOOM

    from_description = g_event(hangoutLink=None, description=f"dial in here {ZOOM} thanks")
    assert events_from_google([from_description], now=NOW)["events"][0]["meeting_url"] == ZOOM


def test_microsoft_link_priority_join_url_then_location_then_body():
    assert events_from_microsoft([m_event()], now=NOW)["events"][0]["meeting_url"] == TEAMS

    from_body = m_event(onlineMeeting=None, bodyPreview=f"join {ZOOM} at nine")
    assert events_from_microsoft([from_body], now=NOW)["events"][0]["meeting_url"] == ZOOM


def test_an_event_with_no_recognizable_link_still_imports_link_less():
    """Fail loud: the terminal renders "bot not armed — no link" rather than the event vanishing."""
    for out in (events_from_google([g_event(hangoutLink=None, location="Room 4")], now=NOW),
                events_from_microsoft([m_event(onlineMeeting=None,
                                               location={"displayName": "Room 4"})], now=NOW)):
        assert len(out["events"]) == 1
        row = out["events"][0]
        assert row["platform"] is None and row["native_meeting_id"] is None
        assert row["meeting_url"] is None
        assert row["title"] == "Weekly sync"


# -------------------------------------------------------------------------------------- time zones

def test_microsoft_naive_datetime_is_read_in_the_stated_zone_not_the_servers():
    row = events_from_microsoft([m_event(start={"dateTime": "2026-09-08T09:00:00.0000000",
                                                "timeZone": "America/New_York"})], now=NOW)["events"][0]
    assert row["scheduled_at"] == "2026-09-08T13:00:00+00:00"  # EDT = UTC-4


def test_an_unresolvable_windows_zone_falls_back_to_utc_not_to_local_time():
    """Wrong by a known offset beats wrong by wherever the pod happens to run (#1316, R-B10)."""
    row = events_from_microsoft([m_event(start={"dateTime": "2026-09-08T09:00:00.0000000",
                                                "timeZone": "Pacific Standard Time"})],
                                now=NOW)["events"][0]
    assert row["scheduled_at"] == "2026-09-08T09:00:00+00:00"


def test_seven_digit_fractional_seconds_parse():
    row = events_from_microsoft([m_event(start={"dateTime": "2026-09-08T09:00:00.1234567",
                                                "timeZone": "UTC"})], now=NOW)["events"][0]
    assert row["scheduled_at"].startswith("2026-09-08T09:00:00.123456")


def test_google_all_day_event_lands_at_midnight_utc():
    row = events_from_google([g_event(start={"date": "2026-09-08"}, end={"date": "2026-09-09"})],
                             now=NOW)["events"][0]
    assert row["scheduled_at"] == "2026-09-08T00:00:00+00:00"


# --------------------------------------------------------------------------------------- attendees

def test_rooms_and_equipment_are_not_people():
    google = events_from_google([g_event(attendees=[
        {"email": "Ann@Example.com", "responseStatus": "tentative"},
        {"email": "room-4@resource.calendar.google.com", "resource": True},
    ])], now=NOW)["events"][0]
    assert google["attendees"] == [{"email": "ann@example.com", "partstat": "tentative"}]

    microsoft = events_from_microsoft([m_event(attendees=[
        {"type": "required", "status": {"response": "notResponded"},
         "emailAddress": {"address": "Bob@Example.com"}},
        {"type": "resource", "emailAddress": {"address": "room-4@example.com"}},
    ])], now=NOW)["events"][0]
    assert microsoft["attendees"] == [{"email": "bob@example.com", "partstat": "needs-action"}]


def test_a_display_name_equal_to_the_email_is_not_a_name():
    row = events_from_google([g_event(attendees=[
        {"email": "ann@example.com", "displayName": "ann@example.com"},
    ])], now=NOW)["events"][0]
    assert row["attendees"] == [{"email": "ann@example.com"}]


def test_the_organizer_response_counts_as_accepted_on_graph():
    row = events_from_microsoft([m_event(attendees=[
        {"type": "required", "status": {"response": "organizer"},
         "emailAddress": {"address": "ann@example.com"}},
    ])], now=NOW)["events"][0]
    assert row["attendees"][0]["partstat"] == "accepted"


# ---------------------------------------------------------------------------------- snapshot policy

def test_the_stored_snapshot_is_bounded_not_the_whole_payload():
    """Unlike the ICS reader's unbounded VEVENT copy (#1213 item 3), a provider event carries far
    more than we want in a meeting row — extended properties, attachments, ACL hints."""
    noisy = g_event(extendedProperties={"private": {"secret": "do-not-store"}},
                    attachments=[{"fileUrl": "https://drive.example/x"}],
                    gadget={"preferences": {"k": "v"}})
    component = events_from_google([noisy], now=NOW)["events"][0]["metadata"]["component"]

    assert "extendedProperties" not in component
    assert "attachments" not in component
    assert "gadget" not in component
    assert component["iCalUID"] == "series-a@google.com"
    assert component["summary"] == "Weekly sync"


def test_the_metadata_names_which_reader_produced_the_row():
    assert events_from_google([g_event()], now=NOW)["events"][0]["metadata"]["provider"] == "google"
    assert events_from_microsoft([m_event()], now=NOW)["events"][0]["metadata"]["provider"] == "microsoft"


def test_the_calendar_the_row_came_from_is_carried_through():
    cal = {"id": "primary", "summary": "Work"}
    row = events_from_google([g_event()], now=NOW, calendar=cal)["events"][0]
    assert row["metadata"]["calendar"] == cal


# ------------------------------------------------------------------------------------- robustness

def test_junk_items_are_skipped_rather_than_killing_the_sweep():
    out = events_from_google([None, "nonsense", {}, {"iCalUID": ""}, g_event()], now=NOW)
    assert len(out["events"]) == 1


def test_an_empty_payload_is_an_empty_result_not_an_error():
    for out in (events_from_google([], now=NOW), events_from_microsoft(None, now=NOW)):
        assert out == {"events": [], "cancelled_uids": []}


def test_horizon_and_lookback_are_caller_supplied_like_parse_ics():
    far = g_event(start={"dateTime": "2026-09-20T13:00:00Z"})
    assert events_from_google([far], now=NOW, horizon_days=7)["events"] == []
    assert len(events_from_google([far], now=NOW, horizon_days=30)["events"]) == 1


# ------------------------------------------------ the whole point: sync_user cannot tell the difference

async def test_sync_user_consumes_a_google_payload_exactly_as_it_consumes_a_feed():
    """The design claim, executed rather than asserted: a provider reader is a new READER, and the
    upsert pipeline underneath it is untouched."""
    store = InMemoryTranscriptStore()
    parsed = events_from_google([g_event(
        attendees=[{"email": "ann@example.com", "displayName": "Ann", "responseStatus": "accepted"}],
    )], now=NOW)

    result = await sync_user(store, 7, parsed, auto_join_default=True)

    assert result["counts"] == {"created": 1, "updated": 0, "cancelled": 0}
    (row,) = await store.list_meetings(7)
    assert row["status"] == "scheduled"
    assert row["data"]["calendar_uid"] == "series-a@google.com"
    assert row["data"]["title"] == "Weekly sync"
    assert row["data"]["auto_join"] is True


async def test_a_calendar_reconnected_over_oauth_adopts_its_own_ics_rows():
    """The migration path. The same meeting, read first from the feed and then from the API, is ONE
    row — because both readers key on the same UID. Get this wrong and every user who switches
    re-imports their whole calendar as duplicates."""
    store = InMemoryTranscriptStore()
    await sync_user(store, 7, parse_ics(ICS, now=NOW), auto_join_default=True)

    result = await sync_user(store, 7, events_from_google([g_event()], now=NOW),
                             auto_join_default=True)

    assert result["counts"]["created"] == 0
    assert len(await store.list_meetings(7)) == 1


async def test_a_series_cancelled_at_the_provider_retires_the_row_the_feed_created():
    store = InMemoryTranscriptStore()
    await sync_user(store, 7, events_from_google([g_event()], now=NOW), auto_join_default=True)

    result = await sync_user(store, 7, events_from_google([g_event(status="cancelled")], now=NOW))

    assert result["counts"]["cancelled"] == 1
