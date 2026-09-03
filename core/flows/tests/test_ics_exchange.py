"""The pilot's own invites are EXCHANGE invites, and `parse_ics` could not read one.

`DTSTART;TZID=W. Europe Standard Time:…` is what Outlook/Exchange writes for Vienna, Berlin and
every other central-European organiser. That string is a WINDOWS zone name, not an IANA one, so
`ZoneInfo(...)` raised `ZoneInfoNotFoundError` — out of `parse_ics`, out of `route`, out of the
mailbox poll. Every Exchange invite was dropped, and dropped by an exception rather than by a
decision, so nothing downstream could even say which meeting had gone missing.

Google invites carry IANA names (`Europe/Lisbon`) and were the only shape ever fixtured, which is
exactly why this went unseen: the parser was exercised only against the calendar the pilot does
not use.

The rule this file fixes in place: **a zone we cannot name is never an exception.** An unknown
TZID falls back to UTC — the same doctrine the floating-DTSTART branch already states one line
below it — because a meeting an hour off still joins, and an invite that raises never joins at
all.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import calendar as cal  # noqa: E402
import time  # noqa: E402
from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from flows_integrations.mailbox import parse_ics  # noqa: E402

ME = "vexa@oenb.at"


def _exchange(*, tzid="W. Europe Standard Time", extra_lines=(), description=None) -> str:
    """One real-shaped Exchange REQUEST. Every detail here is a shape Outlook actually emits and
    Google does not: `MAILTO:` in capitals, a `CN` quoted BECAUSE it contains a comma
    ("Surname, Firstname" is the Exchange directory's own display order), the full
    `ROLE=`/`PARTSTAT=`/`RSVP=` parameter train ahead of the address, and CRLF line endings."""
    # the join link is ALWAYS in the description — without a meeting url there is no invite at
    # all, so `description=` adds the organiser's own prose beside it rather than replacing it.
    desc = ("Join here https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc/0"
            + (" " + description if description else ""))
    return "\r\n".join([
        "BEGIN:VCALENDAR",
        "PRODID:Microsoft Exchange Server 2010",
        "VERSION:2.0",
        "METHOD:REQUEST",
        "BEGIN:VTIMEZONE",
        f"TZID:{tzid}",
        "END:VTIMEZONE",
        "BEGIN:VEVENT",
        'ORGANIZER;CN="Huber, Tobias":MAILTO:Tobias.Huber@oenb.at',
        'ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE;CN="Smith, Anna":'
        "MAILTO:Anna.Smith@oenb.at",
        "ATTENDEE;ROLE=OPT-PARTICIPANT;CN=Ben Meier:MAILTO:ben.meier@oenb.at",
        "ATTENDEE;ROLE=REQ-PARTICIPANT;CN=Vexa Minutes:MAILTO:vexa@oenb.at",
        f"DESCRIPTION:{desc}",
        "UID:040000008200E00074C5B7101A82E00800000000A1B2C3",
        f"DTSTART;TZID={tzid}:20300902T140000",
        "SUMMARY:Planning",
        *extra_lines,
        "END:VEVENT",
        "END:VCALENDAR",
    ])


# ── the headline ────────────────────────────────────────────────────────────────────────────────

def test_an_exchange_invite_parses_at_all():
    """RED BEFORE THE FIX: `ZoneInfoNotFoundError: 'No time zone found with key W. Europe Standard
    Time'`. Not a None, not a quarantine — a raise, on the pilot's only invite shape."""
    ev = parse_ics(_exchange(), self_addr=ME)
    assert ev is not None, "the Exchange invite did not parse"
    assert ev["organizer"] == "tobias.huber@oenb.at"
    assert ev["title"] == "Planning"
    assert ev["url"].startswith("https://teams.microsoft.com/l/meetup-join/")


def test_a_windows_zone_resolves_to_the_right_wall_clock():
    """The fallback must not be reached for a zone we CAN name: `W. Europe Standard Time` is
    Europe/Berlin, so 14:00 on 2 Sep 2030 is 12:00Z (CEST), not 14:00Z. An hour of drift here
    dispatches the bot into an empty room and the organiser sees us miss the meeting."""
    ev = parse_ics(_exchange(), self_addr=ME)
    expected = datetime(2030, 9, 2, 14, 0, 0, tzinfo=ZoneInfo("Europe/Berlin")).timestamp()
    assert ev["start"] == expected


def test_an_unknown_zone_falls_back_to_utc_and_never_raises():
    """A zone name in neither table nor tzdata is a fact we do not have, and the invite is still
    an invite. UTC is what the floating-DTSTART branch already does with the same uncertainty."""
    ev = parse_ics(_exchange(tzid="Middle-earth Standard Time"), self_addr=ME)
    assert ev is not None
    assert ev["start"] == cal.timegm(time.strptime("20300902T140000", "%Y%m%dT%H%M%S"))


def test_iana_tzids_are_untouched():
    """Google's shape still wins on its own terms — the table is consulted, never imposed."""
    ev = parse_ics(_exchange(tzid="Europe/Lisbon"), self_addr=ME)
    expected = datetime(2030, 9, 2, 14, 0, 0, tzinfo=ZoneInfo("Europe/Lisbon")).timestamp()
    assert ev["start"] == expected


# ── the attendees off an Exchange line ──────────────────────────────────────────────────────────

def test_exchange_attendees_and_their_directory_names():
    """Uppercase `MAILTO:`, addresses lowercased, our own mailbox never an attendee, and a `CN`
    quoted because it holds a comma — unquoted, "Smith" is all a reader gets of the name."""
    ev = parse_ics(_exchange(), self_addr=ME)
    assert ev["participants"] == ["anna.smith@oenb.at", "ben.meier@oenb.at"], \
        "the Exchange ATTENDEE lines did not yield the room (our own address must be absent)"
    assert ev["participant_names"] == {"anna.smith@oenb.at": "Smith, Anna",
                                       "ben.meier@oenb.at": "Ben Meier"}


def test_a_folded_exchange_attendee_line_is_one_attendee():
    """Exchange folds at 75 octets, and the parameter train ahead of a long address means the fold
    routinely lands mid-line. Unfolded wrongly, one person becomes zero or two."""
    folded = _exchange().replace(
        "ATTENDEE;ROLE=OPT-PARTICIPANT;CN=Ben Meier:MAILTO:ben.meier@oenb.at",
        "ATTENDEE;ROLE=OPT-PARTICIPANT;CN=Ben Mei\r\n er:MAILTO:ben.meier@oe\r\n nb.at")
    ev = parse_ics(folded, self_addr=ME)
    assert "ben.meier@oenb.at" in ev["participants"]
    assert ev["participant_names"]["ben.meier@oenb.at"] == "Ben Meier"


# ── the per-meeting opt-out (PRD §16.2 item 3) ──────────────────────────────────────────────────

def test_sharing_is_on_by_default():
    """Default ON is the coefficient (PRD §16.2 item 3). An invite that says nothing shares."""
    ev = parse_ics(_exchange(), self_addr=ME)
    assert ev.get("share_opt_out") is not True


def test_noshare_in_the_description_opts_this_meeting_out():
    """The creator's control, in the one place a creator can reach without an admin: the invite
    body. `#noshare` mirrors the `#group:` token this parser already reads."""
    ev = parse_ics(_exchange(description="Quarterly review #noshare"), self_addr=ME)
    assert ev["share_opt_out"] is True


def test_the_opt_out_token_is_case_insensitive_and_reachable_from_the_summary():
    """People type `#NoShare`, and they put it in the title as often as in the body — `#group:`
    is already scanned over the whole ICS for exactly that reason."""
    assert parse_ics(_exchange(description="review #NOSHARE"), self_addr=ME)["share_opt_out"]
    assert parse_ics(_exchange(extra_lines=("X-ALT-DESC:#NoShare",)),
                     self_addr=ME)["share_opt_out"]


def test_a_word_containing_noshare_is_not_the_token():
    """`#noshareholders` is a hashtag about shareholders. A token that fires on a substring turns
    somebody's agenda into a silently suppressed fan-out, which is the failure nobody reports."""
    ev = parse_ics(_exchange(description="agenda: #noshareholders meeting"), self_addr=ME)
    assert ev.get("share_opt_out") is not True
