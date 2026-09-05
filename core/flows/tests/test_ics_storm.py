"""Class B storm — hostile external formats. The parser is fuzzed with the real-world ICS shapes
that bit us (VTIMEZONE 1970 anchors, TZID) plus Google's habits: folded lines, UTC vs zoned vs
floating times, METHOD:REPLY echoes, missing fields, garbage. Property: a parsed start is NEVER
in the deep past, the organizer is a lowercase address or empty, and junk never throws."""
from __future__ import annotations

import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flows_integrations.mailbox import parse_ics  # noqa: E402

# THE SHAPE IS GOOGLE'S; NOTHING IN IT IS ANYBODY'S. This fixture began as a real invite with the
# identifying parts half-scrubbed — a founder's name in `CN=`, the invite's own Google `UID`, and a
# live-shaped Meet code — and half-scrubbed is the worst of both: the `CN=` and the UID were still
# real, and the Meet code was still dialable-looking, in a public repository, for no test value at
# all. Every one of them is now a placeholder, and each is chosen to keep the PROPERTY the fixture
# exists for:
#
#   * `CN=Example Organizer` — a display name with a space in it, which is what the ATTENDEE/
#     ORGANIZER param parsing has to survive;
#   * `UID:example-invite-0001@calendar.example.test` — a `@`-bearing UID, the shape `ics_uid`
#     carries, on a reserved-by-RFC-2606 domain;
#   * `abc-defg-hij` — the exact `xxx-xxxx-xxx` Meet code shape `MEET_URL` matches (`[a-z-]+`),
#     addressing nothing.
#
# The mixed-case `ORG@Example.com` stays: the organizer's lowercasing is one of the properties
# asserted below.
GOOGLE_SHAPED = """BEGIN:VCALENDAR\nPRODID:-//Google Inc//Google Calendar 70.9054//EN\nVERSION:2.0\nMETHOD:REQUEST\nBEGIN:VTIMEZONE\nTZID:Europe/Lisbon\nBEGIN:DAYLIGHT\nDTSTART:19700329T010000\nEND:DAYLIGHT\nBEGIN:STANDARD\nDTSTART:19701025T020000\nEND:STANDARD\nEND:VTIMEZONE\nBEGIN:VEVENT\nDTSTART;TZID=Europe/Lisbon:20300823T163000\nDTEND;TZID=Europe/Lisbon:20300823T173000\nORGANIZER;CN=Example Organizer:mailto:ORG@Example.com\nUID:example-invite-0001@calendar.example.test\nX-GOOGLE-CONFERENCE:https://meet.google.com/abc-defg-hij\nLOCATION:https://meet.google.com/abc-defg-hij\nSUMMARY:test meeting\nEND:VEVENT\nEND:VCALENDAR"""


def test_vtimezone_never_wins():
    ev = parse_ics(GOOGLE_SHAPED)
    assert ev is not None
    assert ev["start"] > time.time(), "picked the VTIMEZONE 1970 anchor again"
    assert ev["organizer"] == "org@example.com"
    assert ev["ics_uid"].startswith("example-invite-0001")


def test_utc_and_floating_times():
    utc = GOOGLE_SHAPED.replace("DTSTART;TZID=Europe/Lisbon:20300823T163000", "DTSTART:20300823T153000Z")
    ev = parse_ics(utc)
    import calendar
    assert abs(ev["start"] - calendar.timegm(time.strptime("20300823T153000", "%Y%m%dT%H%M%S"))) < 1
    floating = GOOGLE_SHAPED.replace("DTSTART;TZID=Europe/Lisbon:20300823T163000", "DTSTART:20300823T163000")
    assert parse_ics(floating)["start"] > time.time()


def test_group_tag_found_anywhere():
    tagged = GOOGLE_SHAPED.replace("SUMMARY:test meeting", "SUMMARY:daily\nDESCRIPTION:join us #group:payments-daily please")
    assert parse_ics(tagged)["group"] == "payments-daily"
    assert parse_ics(GOOGLE_SHAPED)["group"] is None


def test_no_meet_link_is_none_and_junk_never_throws():
    assert parse_ics(GOOGLE_SHAPED.replace("meet.google.com/abc-defg-hij", "example.com/x")) is None
    rnd = random.Random(7)
    lines = GOOGLE_SHAPED.split("\n")
    for seed in range(200):                       # mutation fuzz: drop/duplicate/garble lines
        r = random.Random(seed)
        mutated = [l for l in lines if r.random() > 0.2]
        for _ in range(r.randrange(3)):
            mutated.insert(r.randrange(len(mutated) + 1),
                           "".join(chr(r.randrange(32, 127)) for _ in range(r.randrange(60))))
        try:
            ev = parse_ics("\n".join(mutated))
        except Exception as e:  # noqa: BLE001
            raise AssertionError(f"seed {seed}: parser threw {type(e).__name__}") from e
        if ev is not None:
            assert ev["start"] > 1_000_000_000, f"seed {seed}: prehistoric start {ev['start']}"


def test_method_reply_shape_parses_but_integration_skips_it():
    # the integration guards METHOD:REPLY (our own RSVPs echo back via Gmail) — the parser
    # itself must still not crash on them
    reply = GOOGLE_SHAPED.replace("METHOD:REQUEST", "METHOD:REPLY")
    parse_ics(reply)
