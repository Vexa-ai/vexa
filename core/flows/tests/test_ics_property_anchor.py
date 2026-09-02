"""ICS property names are read at a LINE START — the bug that mailed a DTSTAMP.

Found on the running sim lane, 2026-09-02, by the first rehearsal invite: `rsvp_accept` died with
`SMTPRecipientsRefused: 553 5.1.3 The address is not a valid RFC 5321 address` for the recipient
`20260902t183213z`. That string is the invite's own DTSTAMP.

    ORGANIZER[^:]*:(?:mailto:)?([^\\s]+)        with re.I, and NO anchor

`[^:]*` matches newlines, and `re.I` matches the word "organizer" wherever it appears. An invite
whose UID contained the state name therefore matched inside the UID line, ate greedily forward to
the next colon anywhere in the event — the one in `DTSTAMP:` — and captured what followed.

**It failed loudly only because our own mail double refuses a malformed address.** An ICS whose
SUMMARY read "Organizer sync" would have produced a plausible wrong address instead, and every
touch for that meeting — the RSVP, the ack, the prepare mail, the minutes — would have gone to
somebody else, with the flow reporting success at every step. That is the shape this suite exists
to catch: not a crash, a confident wrong answer.

RFC 5545 §3.1: a property name begins a content line. `_unfold` has already joined continuations
by the time these run, so `re.M` + `^` is the whole fix.

ONE of these tests is the reproduction — `test_the_organizer_is_the_organizer_line_not_the_word_
organizer_anywhere`, which fails on the unfixed regex with exactly `20260902t183213z`. The rest
are ordering guards: `re.search` takes the FIRST match, so the trap bites only when the word
precedes the real property line, and an ICS's property order belongs to whoever produced it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flows_integrations.mailbox import parse_ics  # noqa: E402

ZOOM = "https://us02web.zoom.us/j/84123456789?pwd=aBcD1234efGH"


def _ics(**over) -> str:
    rows = {
        "DTSTART": "20260902T190000Z",
        "DTEND": "20260902T200000Z",
        "UID": "invite-1@example.test",
        "DTSTAMP": "20260902T183213Z",
        "ORGANIZER;CN=Real Person": "mailto:real@rehearse.test",
        "SUMMARY": "DNA TSC 2026-03-02",
        "DESCRIPTION": f"Join Zoom Meeting\\n{ZOOM}",
        "LOCATION": ZOOM,
    }
    rows.update(over)
    body = "\r\n".join(f"{k}:{v}" for k, v in rows.items())
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\n{body}\r\n" \
           "END:VEVENT\r\nEND:VCALENDAR\r\n"


def test_the_organizer_is_the_organizer_line_not_the_word_organizer_anywhere():
    """THE REGRESSION. A UID carrying the word 'organizer' used to hand the flow the DTSTAMP."""
    ev = parse_ics(_ics(UID="rehearse-organizer-invited-2026-03-02@vexa.local"))
    assert ev is not None
    assert ev["organizer"] == "real@rehearse.test"


def test_a_summary_that_says_organizer_does_not_become_the_recipient():
    """An ORDERING GUARD, not a second reproduction — and the distinction matters.

    `re.search` takes the FIRST match, so the trap only bites when the word appears BEFORE the
    real ORGANIZER line; SUMMARY sits after it, so this passes on the unfixed regex too. It is
    here because the property order in an ICS is the producer's choice, not ours: Outlook puts
    SUMMARY first. On the old regex that ordering would have parsed to a PLAUSIBLE wrong address
    rather than a malformed one, and nothing downstream would have refused it."""
    ev = parse_ics(_ics(SUMMARY="Organizer sync with Finance"))
    assert ev["organizer"] == "real@rehearse.test"


def test_a_description_mentioning_the_organizer_is_ignored_too():
    """Same ordering guard as above (DESCRIPTION follows ORGANIZER here)."""
    ev = parse_ics(_ics(DESCRIPTION=f"Ping the organizer if you cannot make it\\n{ZOOM}"))
    assert ev["organizer"] == "real@rehearse.test"


def test_dtstart_is_read_from_its_own_line_for_the_same_reason():
    """`DTSTART` carried the identical unanchored shape, so it is anchored with it. Also an
    ordering guard — a wrong start is a bot dispatched at the wrong moment, or, far enough past,
    an invite silently dropped by the >24h-in-the-past rule."""
    import calendar
    import time
    ev = parse_ics(_ics(UID="dtstart-notes-2026@example.test",
                        DESCRIPTION=f"DTSTART is 7pm, do not be late\\n{ZOOM}"))
    assert ev["start"] == calendar.timegm(time.strptime("20260902T190000", "%Y%m%dT%H%M%S"))


def test_an_ordinary_invite_still_parses_exactly_as_before():
    ev = parse_ics(_ics())
    assert ev["organizer"] == "real@rehearse.test"
    assert ev["title"] == "DNA TSC 2026-03-02"
    assert ev["url"] == ZOOM
    assert ev["ics_uid"] == "invite-1@example.test"


def test_a_folded_organizer_line_survives_the_anchor():
    """RFC 5545 line folding puts a CRLF + space mid-property. `_unfold` joins it BEFORE these
    patterns run, so anchoring must not break the case folding exists for — a Zoom `?pwd=` URL and
    a long CN are both routinely split."""
    ics = _ics().replace("ORGANIZER;CN=Real Person:mailto:real@rehearse.test",
                         "ORGANIZER;CN=Real\r\n  Person:mailto:real@rehearse.test")
    ev = parse_ics(ics)
    assert ev["organizer"] == "real@rehearse.test"
