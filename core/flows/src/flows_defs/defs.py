"""The two proving flows. Steps are FUNCTIONS (typo = registration error, not a 2pm KeyError);
strings exist only in the database, derived from __name__."""
from __future__ import annotations

from flows import Registry
from flows_steps.fakes import INVITE_RECEIVED, MEETING_COMPLETED


def register_flows(reg: Registry):
    s = reg.steps  # already-registered step fns, by name

    invite_to_summary = reg.flow(
        name="invite_to_summary", version=1, on=INVITE_RECEIVED,
        steps=[s["create_meeting"], s["confirm_by_email"], s["await_start"],
               s["dispatch_bot"], s["await_completion"], s["process_transcript"],
               s["commit_summary"], s["email_participants"]])

    post_meeting = reg.flow(
        name="post_meeting", version=1, on=MEETING_COMPLETED,
        steps=[s["await_completion"], s["process_transcript"],
               s["commit_summary"], s["email_participants"]])

    return invite_to_summary, post_meeting
