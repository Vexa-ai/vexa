"""ONE spelling of "now" for every place it enters a turn (Vexa-ai/vexa#1783, turn-time-context).

Friction 2026-10-13: the turn said "now 19:05 WEST" — correct — while the transcript tool's
timestamps were UTC (`…T18:03:12Z`). With no offset written next to the local clock, the agent read
18:03 as local and concluded an hour of transcript was missing. The cure is a now that names its
zone, its abbreviation, its UTC offset and the UTC time, so a UTC timestamp can be reconciled.
"""
from __future__ import annotations

from datetime import datetime, timezone

from shared import clock_line as C

LISBON = "Europe/Lisbon"


def _at(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def test_now_names_zone_abbreviation_offset_and_utc():
    out = C.now_label(LISBON, _at(2026, 10, 13, 18, 6))
    assert out == ("Tuesday 13 October 2026, 19:06 Europe/Lisbon (WEST, UTC+01:00); "
                   "18:06 UTC")


def test_dst_boundary_is_read_from_the_zone_not_a_fixed_offset():
    # Lisbon leaves summer time at 01:00 UTC on 2026-10-25.
    before = C.now_label(LISBON, _at(2026, 10, 25, 0, 30))
    after = C.now_label(LISBON, _at(2026, 10, 25, 1, 30))
    assert "01:30 Europe/Lisbon (WEST, UTC+01:00)" in before and "00:30 UTC" in before
    assert "01:30 Europe/Lisbon (WET, UTC+00:00)" in after and "01:30 UTC" in after


def test_negative_and_fractional_offsets():
    assert "(EDT, UTC-04:00)" in C.now_label("America/New_York", _at(2026, 7, 1, 12, 0))
    assert "(IST, UTC+05:30)" in C.now_label("Asia/Kolkata", _at(2026, 7, 1, 12, 0))


def test_no_zone_or_a_bad_zone_is_utc_said_plainly():
    assert C.now_label("", _at(2026, 10, 13, 18, 6)) == (
        "Tuesday 13 October 2026, 18:06 UTC (no timezone set)")
    assert C.now_label("Mars/Olympus", _at(2026, 10, 13, 18, 6)) == (
        "Tuesday 13 October 2026, 18:06 UTC (no timezone set)")


def test_a_naive_now_is_refused_rather_than_guessed():
    import pytest
    with pytest.raises(ValueError):
        C.now_label(LISBON, datetime(2026, 10, 13, 18, 6))


def test_utc_offset_spelling():
    assert C.utc_offset(LISBON, _at(2026, 10, 13, 18, 6)) == "UTC+01:00"
    assert C.utc_offset("", _at(2026, 10, 13, 18, 6)) == "UTC+00:00"
