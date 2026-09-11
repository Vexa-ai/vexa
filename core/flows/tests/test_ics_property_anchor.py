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
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flows_integrations.mailbox import parse_ics  # noqa: E402

ZOOM = "https://us02web.zoom.us/j/84123456789?pwd=aBcD1234efGH"

#: THE INVITE IS AN HOUR FROM NOW, computed on every run, and this is not tidiness.
#:
#: These six cases were written against a literal `DTSTART:20260902T190000Z` — the rehearsal invite
#: that produced the bug. On 2026-09-03 that date went into the past, `parse_ics`'s own rule (*"a
#: start >24h in the past is a parse artifact or a stale event — never admit it"*) started returning
#: `None`, and all six failed with `TypeError: 'NoneType' object is not subscriptable`. Nothing
#: about the ANCHOR had regressed; the fixture had expired, and it took the regression test for a
#: confident-wrong-address defect down with it — the tests were red for a day about the wrong thing.
#:
#: A fixture whose validity depends on the calendar is a test that grades itself on when it runs.
#: One hour ahead, derived: comfortably inside the 24h rule in both directions and never in the
#: past on any clock, without pinning a year that will do this again.
#: ONE `time.time()` for the whole module, taken at import. `_stamp` is called from `_rows` and
#: again from `_ics`, and a second read of the clock between them would put the two a second apart
#: — which is invisible until the exact-equality assertion on `start` fails once in sixty runs.
_NOW = time.time()


def _stamp(offset_s: float) -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(_NOW + offset_s))


#: The exact epoch the fixture's own DTSTART names — what `parse_ics` must return for it. Read off
#: the same string the ICS carries rather than recomputed from `time.time()`, so the assertion
#: cannot drift by the second the two calls straddle.
def _start_epoch(dtstart: str) -> int:
    import calendar
    return calendar.timegm(time.strptime(dtstart.rstrip("Z"), "%Y%m%dT%H%M%S"))


def _rows(**over) -> dict:
    rows = {
        "DTSTART": _stamp(3600),                    # an hour from now
        "DTEND": _stamp(3600 + 3600),
        "UID": "invite-1@example.test",
        # The DTSTAMP is the value the unanchored regex used to capture and mail. It stays a
        # DIFFERENT time from the DTSTART — the trap only bites when the two are distinguishable.
        "DTSTAMP": _stamp(-600),
        "ORGANIZER;CN=Real Person": "mailto:real@rehearse.test",
        "SUMMARY": "Platform Sync 2026-03-02",
        "DESCRIPTION": f"Join Zoom Meeting\\n{ZOOM}",
        "LOCATION": ZOOM,
    }
    rows.update(over)
    return rows


def _ics(**over) -> str:
    body = "\r\n".join(f"{k}:{v}" for k, v in _rows(**over).items())
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\n{body}\r\n" \
           "END:VEVENT\r\nEND:VCALENDAR\r\n"


def test_the_fixture_is_never_in_the_past():
    """THE GUARD ON THE FIXTURE. Every assertion below reads `ev[...]`, so a fixture `parse_ics`
    refuses fails them all with a `NoneType` subscript and says nothing about the anchor. This one
    fails with the actual reason instead — and it is the test that would have caught the expiry."""
    rows = _rows()
    assert _start_epoch(rows["DTSTART"]) > time.time() - 86400, (
        "the DTSTART fixture is more than 24h in the past, so parse_ics refuses it by design and "
        "every anchor assertion in this file is about a None")
    assert parse_ics(_ics()) is not None


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
    rows = _rows(UID="dtstart-notes@example.test",
                 DESCRIPTION=f"DTSTART is 7pm, do not be late\\n{ZOOM}")
    ev = parse_ics(_ics(**{k: v for k, v in rows.items() if k in ("UID", "DESCRIPTION")}))
    # The DTSTART line's own value, not the DTSTAMP the unanchored pattern used to capture.
    assert ev["start"] == _start_epoch(rows["DTSTART"])
    assert ev["start"] != _start_epoch(rows["DTSTAMP"])


def test_an_ordinary_invite_still_parses_exactly_as_before():
    ev = parse_ics(_ics())
    assert ev["organizer"] == "real@rehearse.test"
    assert ev["title"] == "Platform Sync 2026-03-02"
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
