"""Outlook/Exchange invites parse to EXACT UTC INSTANTS — not to "looks plausible".

Ported from PR Vexa-ai/vexa#1318, which targeted an abandoned base and merged nowhere. The
fixtures beside this file are constructed from documented Outlook/Exchange output, not captured
from a live tenant, so they prove the PARSER and never the connection — `fixtures_exchange/
README.md` says so in its own words and this docstring repeats it because the distinction is the
whole rung.

Four Microsoft shapes, each of which broke the parser this line shipped before today:

  1. `TZID:"W. Europe Standard Time"` — QUOTED. `mailbox._zone` (branch
     `post-meeting-attendee-followup`) owns the Windows→IANA table and is not duplicated here;
     what it was handed was `"W. Europe Standard Time"` WITH THE QUOTES, which matches no table
     row and no tzdata key, so it fell back to UTC — an hour wrong, silently, on the pilot's own
     zone. `tests/test_ics_exchange.py` pins the table; this file pins the quoting.
  2. `Pacific Standard Time` — unquoted, and it says "Standard" in AUGUST. The Windows name is
     the year-round name of the zone; the IANA zone knows about DST. A mapping that got this
     wrong would be an hour out for half the year and look fine in a smoke test.
  3. Folding at 75 octets, splitting the Meet URL, the UID, the ORGANIZER and the `#group:` tag.
  4. UTF-16LE with a BOM, which decoded to mojibake and then parsed as "not an invite" — a
     SILENT ignore.

And one degradation: an unmappable zone name must produce a floating time and never an exception.
"""
from __future__ import annotations

import base64
import calendar
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flows_integrations.mailbox import _zone, parse_ics                # noqa: E402
from flows_integrations.outlook import decode_ics                      # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures_exchange"
SELF = "info@vexa.ai"


def load(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _now() -> float:
    import time
    return time.time()


def utc(y, mo, d, h, mi=0) -> float:
    return float(calendar.timegm((y, mo, d, h, mi, 0, 0, 1, -1)))


# ── the instants ─────────────────────────────────────────────────────────────────────────────
def test_quoted_windows_zone_resolves_to_the_right_instant():
    """14:00 on 2030-03-15 in `"W. Europe Standard Time"` is 13:00Z — CET, because EU summer time
    does not start until 2030-03-31. Getting the zone but not the DATE gives 12:00Z; keeping the
    QUOTES gives 14:00Z, which is the bug this fixture exists for."""
    ev = parse_ics(load("outlook-w-europe.ics"), SELF)
    assert ev is not None
    assert ev["start"] == utc(2030, 3, 15, 13)
    assert ev["organizer"] == "anna.bank@oenb.at"
    assert ev["url"] == "https://meet.google.com/abc-defg-hij"


def test_windows_name_says_standard_all_year_and_the_iana_zone_knows_better():
    """09:00 on 2030-08-15 in `Pacific Standard Time` is 16:00Z — PDT, UTC-7. A parser that took
    the Windows name literally would answer 17:00Z and be wrong for eight months of every year."""
    ev = parse_ics(load("outlook-pacific.ics"), SELF)
    assert ev is not None
    assert ev["start"] == utc(2030, 8, 15, 16)
    assert ev["organizer"] == "ops@example.com"


def test_an_unmappable_quoted_zone_falls_back_to_utc_and_never_raises():
    """A QUOTED name in neither table nor tzdata. `test_ics_exchange.py` already pins the unquoted
    case; this is the same rule reached through Microsoft's other spelling."""
    ev = parse_ics(load("exchange-unknown-tz.ics"), SELF)
    assert ev is not None
    assert ev["start"] == utc(2030, 9, 1, 11)


def test_folding_is_undone_before_anything_is_matched():
    """Outlook folds at 75 octets. Un-unfolded, every one of these is half a value."""
    ev = parse_ics(load("outlook-folded.ics"), SELF)
    assert ev is not None
    assert ev["url"] == "https://meet.google.com/abc-defg-hij"
    assert ev["organizer"] == "anna.bank@oenb.at"
    assert ev["ics_uid"].endswith("0000010000000AABBCCDD")
    assert ev["group"] == "risk-weekly"
    assert ev["start"] == utc(2030, 3, 15, 13)


def test_utf16le_with_a_bom_reaches_the_same_instant():
    """Some Exchange connectors emit UTF-16LE. Decoded as UTF-8 it is mojibake, `BEGIN:VEVENT` is
    not in it, and the invite is IGNORED with no error anywhere."""
    raw = base64.b64decode(load("outlook-utf16le.ics.b64"))
    assert raw[:2] == b"\xff\xfe"
    ev = parse_ics(decode_ics(raw), SELF)
    assert ev is not None
    assert ev["start"] == utc(2030, 3, 15, 13)
    assert ev["title"] == "Quarterly risk review"


def test_the_old_decode_would_have_silently_ignored_it():
    """The counter-proof: without the BOM sniff there is no event block to find."""
    raw = base64.b64decode(load("outlook-utf16le.ics.b64"))
    assert parse_ics(raw.decode(errors="replace"), SELF) is None


# ── the quoting, at its owner ────────────────────────────────────────────────────────────────
def test_zone_strips_microsofts_quotes_from_both_spellings():
    """The whole of this port's contribution to the timezone rule. The table belongs to
    `mailbox._WINDOWS_ZONES`; the quotes are what came off the Exchange wire around it."""
    assert _zone('"W. Europe Standard Time"') is not None
    assert _zone("W. Europe Standard Time") is not None
    assert _zone('"Europe/Vienna"') is not None
    assert _zone('"Customized Time Zone"') is None
    assert _zone("") is None


# ── the storm property, over the Microsoft corpus ────────────────────────────────────────────
def test_mutated_outlook_invites_never_throw_and_never_go_prehistoric():
    """The same property the Google storm asserts, over Microsoft's shapes: junk never raises,
    and a surviving parse never yields a start in the deep past (which would dispatch a bot
    IMMEDIATELY). 300 rounds, seeded, so a failure is reproducible."""
    rnd = random.Random(1315)
    corpus = [load(n) for n in ("outlook-w-europe.ics", "outlook-pacific.ics",
                                "outlook-folded.ics", "exchange-unknown-tz.ics")]
    import time as _t
    floor = _t.time() - 86400
    for _ in range(300):
        text = list(rnd.choice(corpus))
        for _ in range(rnd.randint(1, 12)):
            if not text:
                break
            i = rnd.randrange(len(text))
            op = rnd.randrange(3)
            if op == 0:
                del text[i]
            elif op == 1:
                text[i] = rnd.choice('\r\n ":;=\\ABC0123')
            else:
                text.insert(i, rnd.choice('\r\n ":;=\\ABC0123'))
        ev = parse_ics("".join(text), SELF)     # must not raise
        if ev is not None:
            assert ev["start"] >= floor


def test_an_out_of_range_dtstart_is_a_fact_and_not_an_exception():
    """`\\d{8}T\\d{6}` admits `20301231T240000`; `time.strptime` then raises `ValueError:
    unconverted data remains: 0`, because its `%H` pattern accepts a single digit. Out of
    `parse_ics`, out of `route`, out of the poll — the whole inbox wedges behind one bad invite.
    Found by the storm above, and it was live on this line before this port."""
    bad = load("outlook-w-europe.ics").replace("20300315T140000", "20301231T240000")
    ev = parse_ics(bad, SELF)                   # must not raise
    assert ev is not None
    assert ev["start"] > _now() 
