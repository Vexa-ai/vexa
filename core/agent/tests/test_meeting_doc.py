"""THE MEETING DOC — one page, the transcript in it, grown incrementally (Vexa-ai/vexa#1598).

Founder, live, 2026-09-06: *"we build this doc on meeting iterating with things on transcript and
chat and this doc and connected stuff"*. Iterating is the whole claim, and iterating is what breaks:
the second Expand is the one that re-reads the room from the top, or rewrites the paragraph the
person typed, or drops the widget slot and takes the live transcript off their screen.

So these tests are about the SECOND run, not the first. Four properties, each with a plausible wrong
answer that would have shipped and would not have failed loudly:

  · the cursor advances, and NEVER goes backwards
  · a region edit leaves hand-written text exactly as it was
  · the widget slot survives every write, in every region, in any order
  · a region write is idempotent — running it twice is running it once
"""
from __future__ import annotations

import pytest

from shared import meeting_doc as md

# The page as it is on a desk mid-meeting: frontmatter with a cursor, a paragraph the person typed,
# the widget, and one region an earlier Expand filled.
PAGE = """---
type: meeting
meeting: 147
title: DNA TSC 2026-03-02
transcript_cursor: 2026-09-06T12:04:31.000Z
---

# DNA TSC 2026-03-02

<!-- vexa:transcript meeting=147 -->

I care about the licence question — ask Cottalango before this ends.

## Decisions
<!-- meeting:decisions:start -->
- The CLA follows the ASWF shape.
<!-- meeting:decisions:end -->
"""

HAND_WRITTEN = "I care about the licence question — ask Cottalango before this ends."


def test_the_widget_slot_is_read_and_written_in_one_spelling():
    assert md.has_slot(PAGE)
    assert md.slot_meeting(PAGE) == "147"
    assert md.slot_marker("147") in PAGE
    # quoting and spacing are the same marker — the client accepts these too
    assert md.slot_meeting('<!--vexa:transcript   meeting="a-1"-->') == "a-1"
    assert not md.has_slot("# A page\n\nno widget here\n")


def test_ensure_slot_adds_one_under_the_title_and_never_a_second():
    page = "---\ntype: meeting\n---\n\n# Room\n\nsome prose\n"
    once = md.ensure_slot(page, "9")
    assert once.count("vexa:transcript") == 1
    assert md.slot_meeting(once) == "9"
    # under the heading, above the prose
    assert once.index("# Room") < once.index("vexa:transcript") < once.index("some prose")
    # idempotent, and it does NOT silently repoint a page that already declares a room
    assert md.ensure_slot(once, "9") == once
    assert md.ensure_slot(once, "77") == once
    assert md.slot_meeting(md.ensure_slot(once, "77")) == "9"


def test_a_region_write_leaves_the_hand_written_text_and_the_slot_alone():
    out = md.write_region(PAGE, "decisions", "- The CLA follows the ASWF shape.\n- Ship the widget.")
    assert HAND_WRITTEN in out
    assert md.slot_marker("147") in out
    assert md.read_cursor(out) == "2026-09-06T12:04:31.000Z"
    assert "Ship the widget." in out
    # the region's OWN previous content is what was replaced, and nothing outside it moved
    assert out.count("# DNA TSC 2026-03-02") == 1
    assert out.count("<!-- meeting:decisions:start -->") == 1


def test_the_slot_survives_a_write_to_every_region_in_any_order():
    doc = PAGE
    for key, _title in reversed(md.REGIONS):
        doc = md.write_region(doc, key, f"body for {key}")
    assert md.slot_meeting(doc) == "147"
    assert doc.count("vexa:transcript") == 1
    assert HAND_WRITTEN in doc
    for key, _title in md.REGIONS:
        assert md.read_region(doc, key) == f"body for {key}"


def test_an_absent_region_is_appended_with_its_heading_and_then_reused():
    assert md.read_region(PAGE, "commitments") is None
    first = md.write_region(PAGE, "commitments", "- Dmitry sends the CLA draft.")
    assert "## Commitments" in first
    assert md.read_region(first, "commitments") == "- Dmitry sends the CLA draft."
    second = md.write_region(first, "commitments", "- Dmitry sends the CLA draft.")
    assert second == first                        # twice is once
    third = md.write_region(second, "commitments", "- Dmitry sends the CLA draft.\n- Sam replies.")
    assert third.count("## Commitments") == 1     # not a second section
    assert "Sam replies." in third


def test_an_empty_region_is_not_a_missing_one():
    emptied = md.write_region(PAGE, "decisions", "")
    assert md.read_region(emptied, "decisions") == ""
    assert md.read_region(emptied, "questions") is None


def test_a_region_key_outside_the_set_is_refused():
    with pytest.raises(ValueError):
        md.write_region(PAGE, "thoughts", "anything")
    with pytest.raises(ValueError):
        md.read_region(PAGE, "notes")


def test_the_cursor_advances_forward_only():
    assert md.read_cursor(PAGE) == "2026-09-06T12:04:31.000Z"
    ahead = md.advance_cursor(PAGE, "2026-09-06T12:41:02.000Z")
    assert md.read_cursor(ahead) == "2026-09-06T12:41:02.000Z"
    # BACKWARDS IS A NO-OP: re-feeding a stretch already written up produces a second account of
    # the same ten minutes in a slightly different voice.
    assert md.advance_cursor(ahead, "2026-09-06T11:00:00.000Z") == ahead
    assert md.advance_cursor(ahead, "") == ahead
    assert md.advance_cursor(ahead, "2026-09-06T12:41:02.000Z") == ahead   # equal is not forward


def test_a_page_with_no_cursor_yet_reads_empty_and_takes_one():
    page = "---\ntype: meeting\nmeeting: 9\n---\n\n# Room\n"
    assert md.read_cursor(page) == ""
    took = md.advance_cursor(page, "2026-09-06T09:00:00.000Z")
    assert md.read_cursor(took) == "2026-09-06T09:00:00.000Z"
    assert "type: meeting" in took and "meeting: 9" in took     # frontmatter kept raw
    assert took.endswith("# Room\n")


def test_a_page_with_no_frontmatter_at_all_gains_one_rather_than_losing_its_body():
    took = md.advance_cursor("# Room\n\nprose\n", "2026-09-06T09:00:00.000Z")
    assert md.read_cursor(took) == "2026-09-06T09:00:00.000Z"
    assert "# Room" in took and "prose" in took


def test_the_cursor_and_the_regions_compose_the_way_an_expand_uses_them():
    """One Expand: read the cursor, write what is new, advance. Then a SECOND one on top."""
    doc = md.scaffold(meeting="147", title="DNA TSC 2026-03-02", date="2026-03-02")
    assert md.slot_meeting(doc) == "147"
    assert md.read_cursor(doc) == ""
    for key, _t in md.REGIONS:
        assert md.read_region(doc, key) == ""      # created, empty — not a form to fill in

    doc = md.write_region(doc, "about", "The foundation's technical steering committee.")
    doc = md.advance_cursor(doc, "2026-03-02T10:05:00.000Z")
    doc += "\nA line the person typed themselves.\n"

    doc = md.write_region(doc, "decisions", "- The CLA follows the ASWF shape.")
    doc = md.advance_cursor(doc, "2026-03-02T10:22:00.000Z")

    assert md.read_cursor(doc) == "2026-03-02T10:22:00.000Z"
    assert md.read_region(doc, "about") == "The foundation's technical steering committee."
    assert "A line the person typed themselves." in doc
    assert md.slot_meeting(doc) == "147"


def test_the_report_lands_in_the_same_document_as_the_notes():
    """The flow's post-meeting report is a REGION of this page, not a second file (#1598)."""
    doc = md.write_region(PAGE, "report", "## Summary\n\nThey agreed on the CLA.")
    assert "They agreed on the CLA." in doc
    assert HAND_WRITTEN in doc                      # the person's own line, untouched
    assert md.read_region(doc, "decisions") == "- The CLA follows the ASWF shape."
    assert md.slot_meeting(doc) == "147"
    # a re-run of the drop rewrites its own region and nothing else
    again = md.write_region(doc, "report", "## Summary\n\nThey agreed on the CLA, and on the date.")
    assert md.read_region(again, "decisions") == "- The CLA follows the ASWF shape."
    assert HAND_WRITTEN in again


def test_the_marker_source_is_the_one_the_terminal_reads():
    """`gate:fact-parity` compares these two strings; this pins the Python side's shape so a rename
    here fails a test in this suite too, not only a gate on push."""
    assert "vexa:transcript" in md.SLOT_SOURCE
    assert md.SLOT_SOURCE.count("(") == 1          # exactly one capture group: the meeting id
