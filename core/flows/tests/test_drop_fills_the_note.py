"""The post-meeting drop fills the meeting's note, not only its report region, and gives it its name.

From a real call (2026-10-14): the note minted when the bot was sent kept six empty sections, a
blank transcript cursor and the title "Google Meet meeting" while the meeting had a real name. The
drop now fills each EMPTY section (decisions, commitments, open questions) from the report's own
sections and replaces a placeholder title (frontmatter and H1) with the resolved one; a section or a
title somebody wrote is never touched.
"""
from __future__ import annotations

import flows_defs.production as production

# The agent's scaffold (`core/agent/shared/meeting_doc.scaffold`) for an ad hoc Meet, as minted.
SCAFFOLD = """---
type: meeting
meeting: 100
native: abc-defg-hij
title: Google Meet meeting
date: 2026-10-14
transcript_cursor:
---

# Google Meet meeting

<!-- vexa:transcript meeting=100 -->

## What this is about
<!-- meeting:about:start -->
<!-- meeting:about:end -->

## Decisions
<!-- meeting:decisions:start -->
<!-- meeting:decisions:end -->

## Commitments
<!-- meeting:commitments:start -->
<!-- meeting:commitments:end -->

## People and companies
<!-- meeting:people:start -->
<!-- meeting:people:end -->

## Open questions
<!-- meeting:questions:start -->
<!-- meeting:questions:end -->

## Report
<!-- meeting:report:start -->
<!-- meeting:report:end -->
"""

REPORT = """Planning call for the next meetup.

## Decided
- Date: 8 November.
- Format: 5 talks.

## Committed
- Val — confirm the venue by Friday.

## Open
- Budget timing.
"""


def _region(doc, key):
    a, b = f"<!-- meeting:{key}:start -->", f"<!-- meeting:{key}:end -->"
    return doc[doc.index(a) + len(a):doc.index(b)].strip()


def test_the_drop_fills_the_empty_sections_of_the_note():
    out = production._merge_meeting_doc(SCAFFOLD, "unused", REPORT, "Meetup planning")
    assert "Date: 8 November." in _region(out, "decisions")
    assert "confirm the venue by Friday" in _region(out, "commitments")
    assert "Budget timing." in _region(out, "questions")
    assert "Planning call for the next meetup." in _region(out, "report")
    assert "<!-- vexa:transcript meeting=100 -->" in out


def test_a_section_somebody_wrote_is_kept():
    mine = SCAFFOLD.replace("<!-- meeting:decisions:start -->\n",
                            "<!-- meeting:decisions:start -->\n- Our own decision.\n")
    out = production._merge_meeting_doc(mine, "unused", REPORT, "Meetup planning")
    assert _region(out, "decisions") == "- Our own decision."


def test_a_placeholder_title_takes_the_meetings_name():
    out = production._merge_meeting_doc(SCAFFOLD, "unused", REPORT, "Meetup planning")
    assert 'title: "Meetup planning"' in out
    assert "# Meetup planning\n" in out
    assert "Google Meet meeting" not in out


def test_a_title_a_person_chose_is_never_replaced():
    named = SCAFFOLD.replace("title: Google Meet meeting", "title: Our sync").replace(
        "# Google Meet meeting", "# Our sync")
    out = production._merge_meeting_doc(named, "unused", REPORT, "Meetup planning")
    assert "title: Our sync" in out and "# Our sync" in out


def test_a_placeholder_never_replaces_a_placeholder():
    out = production._merge_meeting_doc(SCAFFOLD, "unused", REPORT, "Google Meet meeting")
    assert out.count("Google Meet meeting") == 2


def test_placeholder_titles():
    for t in ("Google Meet meeting", "Zoom meeting", "Microsoft Teams meeting", "Meeting"):
        assert production.is_placeholder_title(t)
    assert not production.is_placeholder_title("AI meetup: next steps")


def test_a_rerun_is_byte_identical():
    once = production._merge_meeting_doc(SCAFFOLD, "unused", REPORT, "Meetup planning")
    assert production._merge_meeting_doc(once, "unused", REPORT, "Meetup planning") == once


# A NOTE MINTED EMPTY (no sections, no Report region) that the meeting wrote into. The agent mints a
# note with only its frontmatter and the transcript slot; sections arrive with their first content
# (Update notes, the person). The drop must add its Report to THAT page, never replace it with a
# freshly composed one: what the meeting wrote would be lost at the moment it ends.
EMPTY_MINTED_THEN_WRITTEN = """---
type: meeting
meeting: 100
native: abc-defg-hij
title: Google Meet meeting
date: 2026-10-14
transcript_cursor: 41
---

<!-- vexa:transcript meeting=100 -->

## Decisions
<!-- meeting:decisions:start -->
- Keep the venue from last time.
<!-- meeting:decisions:end -->

My own line about catering.
"""


def test_a_minted_note_without_a_report_region_keeps_what_was_written_and_gains_the_report():
    out = production._merge_meeting_doc(EMPTY_MINTED_THEN_WRITTEN, "FRESH DROP", REPORT, "Meetup planning")
    assert "FRESH DROP" not in out
    assert "- Keep the venue from last time." in out and "My own line about catering." in out
    assert "transcript_cursor: 41" in out
    assert "Val — confirm the venue by Friday." in _region(out, "report")
    assert 'title: "Meetup planning"' in out
    assert production._merge_meeting_doc(out, "FRESH DROP", REPORT, "Meetup planning") == out


def test_an_old_drop_without_a_region_is_still_recomposed():
    old = "---\ntype: meeting\nid: x\ntitle: Pilot\n---\n\n# Pilot\n\nold report text\n"
    assert production._merge_meeting_doc(old, "FRESH DROP", REPORT, "Pilot") == "FRESH DROP"
