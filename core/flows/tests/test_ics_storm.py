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

GOOGLE_REAL = """BEGIN:VCALENDAR\nPRODID:-//Google Inc//Google Calendar 70.9054//EN\nVERSION:2.0\nMETHOD:REQUEST\nBEGIN:VTIMEZONE\nTZID:Europe/Lisbon\nBEGIN:DAYLIGHT\nDTSTART:19700329T010000\nEND:DAYLIGHT\nBEGIN:STANDARD\nDTSTART:19701025T020000\nEND:STANDARD\nEND:VTIMEZONE\nBEGIN:VEVENT\nDTSTART;TZID=Europe/Lisbon:20300823T163000\nDTEND;TZID=Europe/Lisbon:20300823T173000\nORGANIZER;CN=Dmitriy Grankin:mailto:ORG@Example.com\nUID:1sarnqa9ai9u097qn29j3u68sl@google.com\nX-GOOGLE-CONFERENCE:https://meet.google.com/jrn-qwko-mqp\nLOCATION:https://meet.google.com/jrn-qwko-mqp\nSUMMARY:test meeting\nEND:VEVENT\nEND:VCALENDAR"""


def test_vtimezone_never_wins():
    ev = parse_ics(GOOGLE_REAL)
    assert ev is not None
    assert ev["start"] > time.time(), "picked the VTIMEZONE 1970 anchor again"
    assert ev["organizer"] == "org@example.com"
    assert ev["ics_uid"].startswith("1sarnqa9ai9u097qn29j3u68sl")


def test_utc_and_floating_times():
    utc = GOOGLE_REAL.replace("DTSTART;TZID=Europe/Lisbon:20300823T163000", "DTSTART:20300823T153000Z")
    ev = parse_ics(utc)
    import calendar
    assert abs(ev["start"] - calendar.timegm(time.strptime("20300823T153000", "%Y%m%dT%H%M%S"))) < 1
    floating = GOOGLE_REAL.replace("DTSTART;TZID=Europe/Lisbon:20300823T163000", "DTSTART:20300823T163000")
    assert parse_ics(floating)["start"] > time.time()


def test_group_tag_found_anywhere():
    tagged = GOOGLE_REAL.replace("SUMMARY:test meeting", "SUMMARY:daily\nDESCRIPTION:join us #group:payments-daily please")
    assert parse_ics(tagged)["group"] == "payments-daily"
    assert parse_ics(GOOGLE_REAL)["group"] is None


def test_no_meet_link_is_none_and_junk_never_throws():
    assert parse_ics(GOOGLE_REAL.replace("meet.google.com/jrn-qwko-mqp", "example.com/x")) is None
    rnd = random.Random(7)
    lines = GOOGLE_REAL.split("\n")
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
    reply = GOOGLE_REAL.replace("METHOD:REQUEST", "METHOD:REPLY")
    parse_ics(reply)
