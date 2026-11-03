"""F-D20 (a) — a step that never ran must not render as one that did.

`STEP_KINDS` maps `process_meeting` to `report.written`, and `event_from_receipt` derived a
receipt's status from its STATE alone. Both a `not_present` outcome and the engine's `skipped`
outcome are written as CONFIRMED receipts — deliberately, so a redelivery of the same fact cannot
re-run the step — so a `process_meeting` that was answered for rather than run came out as
`report.written`, status `done`, which `render.line` then hides because `done` is the boring
status. The timeline of a no-agents deployment claimed a report for every meeting it held.

That is the same failure `receipts.fail` was added for one row along ("the one thing an agent must
not do is talk about a report it never delivered"), on the other terminal branch.

Both engine spellings are covered here because both exist and they are written by different code
paths: `NotPresent` (`{"outcome": "not_present", "domain": ...}`, `loop._not_present`) and the
`skip` policy (`{"outcome": "skipped", "domain": ..., "skipped": ...}`, `loop._skipped`).
"""
from __future__ import annotations

import flows_timeline.model as tm
import pytest
from flows_timeline.render import line


def _receipt(result: dict, step: str = "process_meeting") -> dict:
    return {"attempted_at": 1_700_000_000.0, "confirmed_at": 1_700_000_001.0,
            "step": step, "state": "confirmed", "result": result, "provider_ref": ""}


NOT_PRESENT = {"outcome": "not_present", "domain": "agent",
               "detail": "this deployment does not run agent"}
SKIPPED = {"outcome": "skipped", "domain": "agent", "skipped": "agent:not_present",
           "detail": "this deployment does not run agent"}


@pytest.mark.parametrize("result", [NOT_PRESENT, SKIPPED], ids=["not_present", "skipped"])
def test_a_step_answered_for_by_the_engine_renders_as_skipped_not_as_done(result):
    ev = tm.event_from_receipt(_receipt(result), {"title": "Weekly sync"}, flow="post_meeting")
    assert ev is not None
    assert ev.status == "skipped (agent:not_present)", ev.status
    assert ev.detail and "agent" in ev.detail


@pytest.mark.parametrize("result", [NOT_PRESENT, SKIPPED], ids=["not_present", "skipped"])
def test_the_rendered_line_says_so_where_a_person_reads_it(result):
    """`render.line` prints a status only when it is NOT one of the boring ones, so a status of
    `done` is INVISIBLE — the defect was not merely a wrong word, it was no word at all."""
    ev = tm.event_from_receipt(_receipt(result), {"title": "Weekly sync"}, flow="post_meeting")
    text = line(ev.as_dict(), "", "2026-09-03")
    assert "[skipped (agent:not_present)]" in text, text


def test_a_real_confirmed_effect_still_renders_as_done():
    """The half a blanket change would break: a step that DID run keeps its quiet `done`."""
    ev = tm.event_from_receipt(
        _receipt({"report": "## Decided", "message_id": "<m@test>"}), {"title": "Weekly sync"},
        flow="post_meeting")
    assert ev.status == "done"
    assert "[" not in line(ev.as_dict(), "", "2026-09-03")


def test_the_pre_existing_skipped_shape_is_unchanged():
    """A STEP'S OWN `Done({"skipped": ...})` — `mail_minutes is off for this person`, and the
    attendee fan-out's typed no-op — carries no domain and must keep reading plainly `skipped`."""
    ev = tm.event_from_receipt(
        _receipt({"skipped": "mail_minutes is off for this person"}, step="email_minutes"),
        {"title": "Weekly sync"}, flow="post_meeting")
    assert ev.status == "skipped"
    assert ev.detail == "mail_minutes is off for this person"
