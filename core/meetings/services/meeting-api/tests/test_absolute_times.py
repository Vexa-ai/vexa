"""Golden for `_fill_absolute_times` — the REST transcript absolute-time derivation.

Regression: a live-pipeline segment carries `start` as ABSOLUTE epoch-seconds (~1.78e9). The old
derivation did `base + timedelta(seconds=start)` unconditionally, adding an absolute epoch to the
meeting-start datetime → year 2083 (witnessed on v0.12.2: `2083-01-23T...` served over REST while
redis held the correct 2026 time). The fix discriminates absolute vs relative by magnitude.
"""
from datetime import datetime, timezone

from meeting_api.collector.adapters import _fill_absolute_times


def test_absolute_epoch_start_is_used_directly_not_added_to_base():
    # The witnessed segment: start = absolute epoch-seconds for 2026-07-13T20:46:21Z.
    base = datetime(2026, 7, 13, 20, 46, 15, tzinfo=timezone.utc)  # meeting start
    segs = [{"start": 1783975581.012, "end": 1783975592.788, "text": "1, 2, 3, 4, 5"}]

    _fill_absolute_times(segs, base)

    # Must be the SAME instant as `start` (2026), NOT base+start (2083).
    got = datetime.fromisoformat(segs[0]["absolute_start_time"])
    assert got.year == 2026, f"regressed to {got.isoformat()} (the 2083 double-count)"
    assert abs(got.timestamp() - 1783975581.012) < 1.0
    assert datetime.fromisoformat(segs[0]["absolute_end_time"]).year == 2026


def test_negative_control_relative_offset_still_anchors_to_base():
    # A genuine relative offset (the carve): small seconds-since-start → base + offset.
    base = datetime(2026, 7, 13, 20, 46, 15, tzinfo=timezone.utc)
    segs = [{"start": 12.0, "end": 18.5, "text": "hi"}]

    _fill_absolute_times(segs, base)

    got = datetime.fromisoformat(segs[0]["absolute_start_time"])
    assert got.year == 2026
    assert abs((got - base).total_seconds() - 12.0) < 0.001  # anchored, not treated as epoch


def test_producer_supplied_absolute_time_is_left_untouched():
    segs = [{"start": 1783975581.0, "absolute_start_time": "2026-07-13T20:46:21+00:00"}]
    _fill_absolute_times(segs, datetime(2026, 7, 13, tzinfo=timezone.utc))
    assert segs[0]["absolute_start_time"] == "2026-07-13T20:46:21+00:00"
