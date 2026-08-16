"""L1/L2 — the pure parser: MIME shapes, the two calendar flavours, and RFC 5545 details.

``parse_invite`` is the half that has to be right about other people's software, so it is pinned
directly here (the corpus asserts the SERVICE; this asserts the PARSER). Nothing in this file
touches a port.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from conftest import NOW, envelope, read_eml, read_ics

from vexa_mailroom import parse_invite
from vexa_mailroom.invite import METHOD_CANCEL, METHOD_REQUEST


def parse(name: str, **kw):
    return parse_invite(envelope(read_ics(name), **kw), now=NOW)


def test_google_request():
    p = parse("google-request-meet.ics")
    assert p.ok and p.method == METHOD_REQUEST
    assert p.uid == "google-one-off-001@google.com"
    assert p.sequence == 0
    assert p.summary == "Quarterly sync"
    assert p.dtstart == "2026-08-18T14:00:00+00:00"
    assert (p.platform, p.native_meeting_id) == ("google_meet", "abc-defg-hij")
    assert "mk-dev@dev.vexa.ai" in p.attendees
    assert p.organizer == "organizer@example.com"
    assert p.recurring is False


def test_outlook_request_carries_a_timezone():
    """Outlook writes DTSTART in a VTIMEZONE-declared local time — it must land as UTC."""
    p = parse("outlook-request-teams.ics")
    assert p.ok
    assert p.dtstart == "2026-08-20T09:00:00+00:00"          # 11:00 CEST
    assert p.platform == "teams"
    assert p.native_meeting_id == "19:meeting_ZjQxYjkwMjEt@thread.v2"


def test_recurring_series_reports_the_next_occurrence():
    p = parse("google-recurring-weekly.ics")
    assert p.ok and p.recurring is True
    assert p.rrule and "FREQ=WEEKLY" in p.rrule
    assert p.dtstart == "2026-08-19T10:00:00+00:00"


def test_exdate_is_respected():
    p = parse("google-recurring-exdate.ics")
    assert p.dtstart == "2026-08-26T10:00:00+00:00"


def test_sequence_is_read_and_defaults_to_zero():
    assert parse("google-recurring-update-seq2.ics").sequence == 2
    assert parse("google-request-meet.ics").sequence == 0


def test_cancel_method():
    p = parse("google-cancel.ics")
    assert p.ok and p.method == METHOD_CANCEL and p.uid == "google-one-off-001@google.com"


def test_status_cancelled_without_method_reads_as_cancel():
    p = parse("outlook-cancel-status-only.ics")
    assert p.ok and p.method == METHOD_CANCEL and p.status == "CANCELLED"


def test_a_cancel_needs_no_link_or_time():
    """A cancellation carries whatever the sender kept — it must not be rejected for thinness."""
    ics = ("BEGIN:VCALENDAR\nVERSION:2.0\nMETHOD:CANCEL\nBEGIN:VEVENT\n"
           "UID:thin-cancel@example.com\nSEQUENCE:3\nDTSTAMP:20260816T090000Z\n"
           "END:VEVENT\nEND:VCALENDAR\n")
    p = parse_invite(envelope(ics), now=NOW)
    assert p.ok and p.method == METHOD_CANCEL and p.native_meeting_id is None


@pytest.mark.parametrize("name,reason", [
    ("negative-no-link.ics", "no_meeting_link"),
    ("negative-no-uid.ics", "no_uid"),
    ("negative-malformed.ics", "unparseable_ics"),
    ("negative-reply-rsvp.ics", "unsupported_method"),
])
def test_rejections(name, reason):
    p = parse(name)
    assert not p.ok
    assert p.rejection.reason == reason
    assert p.rejection.detail


def test_a_plain_email_is_not_an_invitation():
    p = parse_invite(read_eml("negative-plain-email.eml"), now=NOW)
    assert not p.ok and p.rejection.reason == "no_calendar_part"


def test_multipart_google_invitation():
    p = parse_invite(read_eml("google-invitation.eml"), now=NOW)
    assert p.ok and p.uid == "google-one-off-001@google.com"
    assert "mk-dev@dev.vexa.ai" in p.recipients


def test_base64_ics_attachment_only():
    """Exchange relays sometimes ship the invite ONLY as an octet-stream attachment."""
    p = parse_invite(read_eml("outlook-attachment-only.eml"), now=NOW)
    assert p.ok and p.platform == "teams"


def test_content_type_method_is_used_when_the_body_omits_it():
    ics = read_ics("google-request-meet.ics").replace("METHOD:REQUEST\n", "")
    p = parse_invite(envelope(ics, method="REQUEST"), now=NOW)
    assert p.ok and p.method == METHOD_REQUEST


def test_a_bare_ics_with_no_method_at_all_is_treated_as_a_request():
    ics = read_ics("google-request-meet.ics").replace("METHOD:REQUEST\n", "")
    p = parse_invite(envelope(ics), now=NOW)
    assert p.ok and p.method == METHOD_REQUEST
    assert any("assumed REQUEST" in w for w in p.warnings)


def test_rooms_and_resources_are_not_attendees():
    ics = read_ics("google-request-meet.ics").replace(
        "X-GOOGLE-CONFERENCE:",
        "ATTENDEE;CUTYPE=ROOM;CN=Board Room:mailto:board-room@example.com\r\nX-GOOGLE-CONFERENCE:")
    p = parse_invite(envelope(ics), now=NOW)
    assert "board-room@example.com" not in p.attendees


def test_invited_addresses_are_the_attendee_list_only():
    """``invited_addresses`` binds and comes from ATTENDEE; ``addressed_to`` adds the envelope."""
    p = parse("google-request-meet.ics", to="ops@example.com")
    assert "ops@example.com" not in p.invited_addresses
    assert "mk-dev@dev.vexa.ai" in p.invited_addresses
    assert {"ops@example.com", "mk-dev@dev.vexa.ai"} <= set(p.addressed_to)


def test_roster_keeps_role_and_partstat():
    ics = read_ics("google-request-meet.ics").replace(
        "CN=Vexa Mailroom;X-NUM-GUESTS=0:mailto:mk-dev@dev.vexa.ai",
        "ROLE=OPT-PARTICIPANT;PARTSTAT=NEEDS-ACTION;CN=Vexa Mailroom:mailto:mk-dev@dev.vexa.ai")
    p = parse_invite(envelope(ics), now=NOW)
    row = next(r for r in p.participants if r["email"] == "mk-dev@dev.vexa.ai")
    assert row["role"] == "OPT-PARTICIPANT"                # optional invitations still bind
    assert row["partstat"] == "NEEDS-ACTION"
    assert row["name"] == "Vexa Mailroom"


def test_floating_dtstart_is_refused_not_guessed():
    ics = read_ics("google-request-meet.ics").replace("DTSTART:20260818T140000Z",
                                                      "DTSTART:20260818T140000")
    p = parse_invite(envelope(ics), now=NOW)
    assert not p.ok and p.rejection.reason == "floating_start_time"


def test_an_all_day_event_is_refused_for_the_same_reason():
    ics = read_ics("google-request-meet.ics").replace("DTSTART:20260818T140000Z",
                                                      "DTSTART;VALUE=DATE:20260818")
    p = parse_invite(envelope(ics), now=NOW)
    assert not p.ok and p.rejection.reason == "floating_start_time"


def test_recurrence_id_override_still_carries_the_series_uid():
    """A single-occurrence override arrives with its series UID — v0 binds series, so it updates."""
    ics = read_ics("google-recurring-weekly.ics").replace(
        "DTSTART:20260805T100000Z",
        "RECURRENCE-ID:20260819T100000Z\r\nDTSTART:20260819T160000Z")
    p = parse_invite(envelope(ics), now=NOW)
    assert p.ok and p.uid == "google-recurring-006@google.com"


def test_far_future_series_binds_even_when_the_next_occurrence_is_beyond_the_horizon():
    ics = read_ics("google-recurring-weekly.ics").replace(
        "RRULE:FREQ=WEEKLY;BYDAY=WE", "RRULE:FREQ=YEARLY;BYMONTH=12;BYMONTHDAY=25")
    p = parse_invite(envelope(ics), now=NOW, horizon_days=7)
    assert not p.ok and p.rejection.reason == "no_start_time"


def test_parse_never_raises_on_garbage():
    for raw in (b"", b"\x00\x01\x02", b"Subject: x\r\n\r\nbody", b"BEGIN:VCALENDAR"):
        p = parse_invite(raw, now=datetime(2026, 8, 16, tzinfo=timezone.utc))
        assert not p.ok and p.rejection.reason
