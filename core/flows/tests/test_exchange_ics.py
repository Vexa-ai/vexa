"""Outlook/Exchange ICS — the shapes a bank's mailbox actually emits.

Google's invites are UTF-8, IANA-zoned and unfolded in practice; Outlook's are none of those.
Each test below pins an EXACT UTC instant, because the failure this guards against is not a
crash — it is a bot dispatched an hour early or a year into the future.

Fixtures + provenance: `tests/fixtures_exchange/README.md`."""
from __future__ import annotations

import base64
import calendar
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from flows import SqliteDB  # noqa: E402
from flows_integrations.ics import (  # noqa: E402
    WINDOWS_TO_IANA,
    decode_ics,
    parse_ics,
    resolve_tzid,
    unfold,
)
from flows_integrations.mailbox import route  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures_exchange"


def load(name: str) -> str:
    return FIX.joinpath(name).read_text()


def utc(stamp: str) -> float:
    return calendar.timegm(time.strptime(stamp, "%Y%m%dT%H%M%S"))


def test_quoted_windows_zone_resolves_to_the_exact_utc_instant():
    ev = parse_ics(load("outlook-w-europe.ics"))
    assert ev is not None
    # 2030-03-15 14:00 in "W. Europe Standard Time" (Europe/Berlin). EU DST starts 2030-03-31,
    # so this date is CET (UTC+1) → 13:00Z. An unmapped zone would land on 14:00Z or throw.
    assert ev["start"] == utc("20300315T130000")
    assert ev["organizer"] == "anna.bank@oenb.at"
    assert ev["url"] == "https://meet.google.com/abc-defg-hij"
    assert ev["title"] == "Quarterly risk review"
    assert ev["ics_uid"].startswith("040000008200E00074C5B7101A82E008")


def test_second_windows_zone_unquoted():
    ev = parse_ics(load("outlook-pacific.ics"))
    # 2030-08-15 09:00 "Pacific Standard Time" = America/Los_Angeles, in PDT (UTC-7) → 16:00Z.
    # The Windows name says "Standard" all year; the IANA zone is what knows about DST.
    assert ev["start"] == utc("20300815T160000")
    assert ev["organizer"] == "ops@example.com"


def test_the_1601_vtimezone_anchor_never_wins():
    """Outlook anchors its VTIMEZONE rules at 16010101 — the Exchange sibling of the Google 1970
    bug. A start in the deep past is refused outright, so a bot can never dispatch immediately."""
    for name in ("outlook-w-europe.ics", "outlook-pacific.ics"):
        assert parse_ics(load(name))["start"] > time.time()


def test_folded_lines_are_unfolded_before_anything_is_matched():
    raw = load("outlook-folded.ics")
    assert "https://meet.google.com/abc-defg-hij" not in raw, "fixture must actually be folded"
    ev = parse_ics(raw)
    assert ev["url"] == "https://meet.google.com/abc-defg-hij"        # URL split across lines
    assert ev["organizer"] == "anna.bank@oenb.at"                     # address split across lines
    assert ev["ics_uid"].endswith("AABBCCDD")                         # UID split across lines
    assert ev["group"] == "risk-weekly"                              # the group tag, split too
    assert ev["start"] == utc("20300315T130000")


def test_unmappable_zone_degrades_to_floating_and_never_raises():
    """A ZoneInfoNotFoundError here would wedge the cursor: the poller only advances after a
    message is routed, so ONE hand-built Outlook zone would stall the whole inbox forever."""
    assert resolve_tzid("Customized Time Zone") is None
    ev = parse_ics(load("exchange-unknown-tz.ics"))
    assert ev is not None
    assert ev["start"] == time.mktime(time.strptime("20300901T110000", "%Y%m%dT%H%M%S"))


def test_utf16le_with_bom_decodes():
    raw = base64.b64decode(load("outlook-utf16le.ics.b64"))
    assert raw[:2] == b"\xff\xfe"
    ev = parse_ics(decode_ics(raw))
    assert ev["start"] == utc("20300315T130000")
    assert ev["organizer"] == "anna.bank@oenb.at"


def test_windows_map_is_the_cldr_world_default_table():
    assert WINDOWS_TO_IANA["W. Europe Standard Time"] == "Europe/Berlin"
    assert WINDOWS_TO_IANA["GMT Standard Time"] == "Europe/London"      # NOT Etc/GMT
    assert WINDOWS_TO_IANA["India Standard Time"] == "Asia/Calcutta"
    assert len(WINDOWS_TO_IANA) > 100
    from zoneinfo import ZoneInfo
    for win, iana in WINDOWS_TO_IANA.items():
        assert ZoneInfo(iana), win                                     # every target resolves


def test_exchange_request_routes_as_an_invite_and_reply_is_ignored():
    db = SqliteDB()
    ics = load("outlook-w-europe.ics")
    kind, ev = route(db, "info@vexa.ai", "anna.bank@oenb.at", {}, ics, lambda e: None,
                     lambda u: False)
    assert kind == "invite" and ev["start"] == utc("20300315T130000")
    echo = ics.replace("METHOD:REQUEST", "METHOD:REPLY")
    assert route(db, "info@vexa.ai", "anna.bank@oenb.at", {}, echo, lambda e: None,
                 lambda u: False) is None


def test_exchange_fixtures_survive_mutation_fuzz():
    """Same property as the Google storm: junk never throws, and a surviving parse is never
    prehistoric — now over the Outlook shapes as well."""
    corpus = [load(n) for n in ("outlook-w-europe.ics", "outlook-folded.ics",
                               "outlook-pacific.ics", "exchange-unknown-tz.ics")]
    for seed in range(300):
        r = random.Random(seed)
        lines = r.choice(corpus).split("\r\n")
        mutated = [l for l in lines if r.random() > 0.2]
        for _ in range(r.randrange(3)):
            mutated.insert(r.randrange(len(mutated) + 1),
                           "".join(chr(r.randrange(32, 127)) for _ in range(r.randrange(60))))
        try:
            ev = parse_ics("\r\n".join(mutated))
        except Exception as e:  # noqa: BLE001
            raise AssertionError(f"seed {seed}: parser threw {type(e).__name__}") from e
        if ev is not None:
            assert ev["start"] > 1_000_000_000, f"seed {seed}: prehistoric start {ev['start']}"


def test_unfold_is_rfc5545_and_leaves_ordinary_text_alone():
    assert unfold("A:one\r\n two") == "A:onetwo"
    assert unfold("A:one\n\ttwo") == "A:onetwo"
    assert unfold("A:one\r\nB:two") == "A:one\nB:two"
