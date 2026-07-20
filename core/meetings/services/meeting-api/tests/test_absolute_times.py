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


def test_naive_base_stamps_utc_offset_matching_sealed_api_v1_format():
    # Production hot path: `base` is a PG TIMESTAMP WITHOUT TIME ZONE → a NAIVE datetime. The derived
    # absolute times must STILL carry the sealed api.v1 `+00:00` offset, or the dashboard's `new Date()`
    # reads them as viewer-local. Covers the `base.tzinfo is None` UTC stamp in _fill_absolute_times.
    naive_base = datetime(2026, 7, 13, 20, 46, 15)  # no tzinfo — the real store's shape
    segs = [{"start": 12.0, "end": 18.5, "text": "hi"}]

    _fill_absolute_times(segs, naive_base)

    assert segs[0]["absolute_start_time"].endswith("+00:00")
    assert segs[0]["absolute_end_time"].endswith("+00:00")
    got = datetime.fromisoformat(segs[0]["absolute_start_time"])
    assert got.utcoffset() is not None and got.utcoffset().total_seconds() == 0
    assert abs((got - naive_base.replace(tzinfo=timezone.utc)).total_seconds() - 12.0) < 0.001


def test_corrupt_epoch_value_skips_segment_without_raising():
    # A misencoded producer value (e.g. a wall-clock in MILLISECONDS) routes into the epoch branch but
    # is out of datetime range. It must only drop THAT segment's absolute times — never raise, which
    # would 500 the whole transcript endpoint. Guards the graceful-degradation try/except.
    base = datetime(2026, 7, 13, 20, 46, 15, tzinfo=timezone.utc)
    segs = [
        {"start": 1780000000000.0, "end": 1780000005000.0, "text": "ms-epoch garbage"},  # out of range
        {"start": 12.0, "end": 18.5, "text": "good"},  # a valid relative offset alongside it
    ]

    _fill_absolute_times(segs, base)  # must NOT raise

    assert "absolute_start_time" not in segs[0]  # the corrupt segment is skipped
    assert segs[1]["absolute_start_time"].endswith("+00:00")  # the good one still renders
