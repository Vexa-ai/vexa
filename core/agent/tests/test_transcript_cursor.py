"""`transcript_terms(since=…)`: the cursor compares as a time, and an empty answer says why.

Friction report (dogfood, 2026-10-10): a Highlight on a finished meeting passed the meeting page's
own cursor, got zero segments and a note that the cursor was past the end, then got zero again
without `since`, and only a third call returned terms. Two things are pinned here: a cursor written
in one timestamp spelling never hides later segments written in another, and an answer with no
segments says whether the transcript is empty or nothing was said after the cursor.
"""
from __future__ import annotations

from control_plane import meeting_highlight as H
from shared import terms as T

SEGS = [
    {"absolute_start_time": "2026-10-09T14:07:01.000000+00:00", "text": "Robin Vale opened."},
    {"absolute_start_time": "2026-10-09T14:52:57.309100+00:00", "text": "Example Bank closed it."},
]


def test_a_z_cursor_does_not_hide_a_later_offset_segment():
    # string order says "…14:52:57Z" > "…14:52:57.309100+00:00"; time order says the opposite
    fresh = T.segments_since(SEGS, "2026-10-09T14:52:57Z")
    assert [g["text"] for g in fresh] == ["Example Bank closed it."]


def test_the_cursor_at_the_last_segment_returns_nothing_and_says_so():
    out = H.scan(SEGS, [], since="2026-10-09T14:52:57.309100Z")
    assert out["scanned_segments"] == 0 and out["transcript_segments"] == 2
    assert "nothing was said after" in out["cursor_note"] and "Omit since" in out["cursor_note"]


def test_no_since_scans_the_whole_meeting():
    out = H.scan(SEGS, [])
    assert out["scanned_segments"] == 2 and out["cursor_note"] == ""
    assert {t["term"] for t in out["terms"]} >= {"Robin Vale", "Example Bank"}


def test_an_empty_transcript_is_named_as_empty_not_as_a_cursor_problem():
    out = H.scan([], [], since="2026-10-09T14:00:00Z")
    assert out["transcript_segments"] == 0
    assert out["cursor_note"] == "the transcript has no segments yet"


def test_relative_starts_compare_as_numbers():
    rel = [{"start": 9.5, "text": "a"}, {"start": 10.25, "text": "b"}, {"start": 100, "text": "c"}]
    assert [g["text"] for g in T.segments_since(rel, "10")] == ["b", "c"]
