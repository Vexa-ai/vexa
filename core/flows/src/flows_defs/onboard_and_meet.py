"""THE PRODUCT FLOW (founder spec 2026-08-23):

  vexa@bank.com invite → organizer notified, bot scheduled
    ├─ no personal workspace? → from-email name+company research → ONE question by email →
    │  converse until the personal workspace is properly set up (.scaffolded)
    ├─ bot joins at START−2min, retries inside the meeting window
    └─ after the meeting: processing QUEUES BEHIND workspace readiness — blocked, following the
       human up on a cadence — then the agent updates the workspace, commits the summary, emails.

Three flows, chained by facts; the deferred-processing queue is one `Block` + follow-up waits.

UI-LESS CONSTRAINT (founder 2026-08-23): email is the ENTIRE product surface — every artifact
(confirmation, onboarding question, nudge, the meeting note itself) travels IN the email body,
verbatim from the committed file. No UI links anywhere; replying to the email is the interface."""
from __future__ import annotations

from flows import Registry
from flows_steps.fakes import INVITE_RECEIVED, MEETING_COMPLETED
from flows import EventType

ONBOARDING_NEEDED = EventType("onboarding.needed")


def register(reg: Registry):
    s = reg.steps

    # ── flow 1: the invite ────────────────────────────────────────────────────
    invite = reg.flow(
        name="invite_intake", version=1, on=INVITE_RECEIVED,
        steps=[s["create_meeting"],          # planned row from the ICS (idempotent claim)
               s["notify_organizer"],        # "Vexa will join at <time>" — Mailpit
               s["ensure_onboarding"],       # no personal workspace? EMIT onboarding.needed (sub-flow)
               s["await_start"],             # time is a column: due = start − 120s
               s["dispatch_bot"]])           # retries inside the window are the engine's backoff

    # ── flow 2: onboarding by email (runs only when flow 1 emitted the fact) ──
    onboarding = reg.flow(
        name="onboard_by_email", version=1, on=ONBOARDING_NEEDED,
        steps=[s["research_person"],         # from-email → name, company, public footprint
               s["ask_one_question"],        # ONE email: confirm identity + the missing facts
               s["await_human_reply"],       # Block; each reply is a resume-signal; re-asks follow up
               s["setup_personal_workspace"]])  # scaffold + .scaffolded → the readiness fact

    # ── flow 3: post-meeting, queued behind readiness ─────────────────────────
    post = reg.flow(
        name="post_meeting_gated", version=1, on=MEETING_COMPLETED,
        steps=[s["await_completion"],
               s["require_workspace"],       # ready? pass. Not? Block + follow-up emails on cadence
               s["process_transcript"],
               s["commit_summary"],
               s["email_participants"]])
    return invite, onboarding, post
