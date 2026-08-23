"""PRODUCTION flows (founder spec 2026-08-23, evening scope):

  1. invite_intake      — info@vexa.ai invited → user ensured → iMIP ACCEPT in the calendar →
                          ack email (+ finalize-workspace ask if needed) → personal onboarding
                          spawned · #group:name → group onboarding spawned → bot at start−2min →
                          meeting → completed fact
  2. onboard_person     — a REAL agent conversation over email (threaded) until the AGENT
                          writes `.scaffolded`; silence is chased with nudges
  3. onboard_group      — same conversation pattern for the #group workspace, chased by email
  4. post_meeting       — gated on `.scaffolded` → agent processes through the workspace →
                          summary + action points VERBATIM by email, asking for feedback
  5. email_chat         — every threaded reply becomes an agent turn (feedback processed, the
                          workspace updated) and the agent's answer goes back by email: the
                          standing conversation. UI-less: email is the entire surface.

Laws (from the live witness): steps never sleep · all state in refs/receipts · replies by thread."""
from __future__ import annotations

import time

from flows import Done, Registry, StepCtx, StepError, Wait, EventType

from flows_steps import agent as ag
from flows_steps import emailx as mx
from flows_steps import meeting as mt
from flows_steps.common import ensure_platform_user, scaffolded, ws_file

INVITE = EventType("invite.received")
ONB_PERSON = EventType("onboarding.person.needed")
ONB_GROUP = EventType("onboarding.group.needed")
COMPLETED = EventType("meeting.completed")
MAIL_REPLY = EventType("mail.reply")

NUDGE_EVERY_S = 15 * 60

ONBOARD_KICKOFF = """[email-onboarding] You are onboarding this person OVER EMAIL — every reply you
write is sent verbatim as a plain-text email; their replies come back as your next turn. Read
flows/personal.md and CLAUDE.md; run the discovery loop adapted to email: research what you can
yourself, ask ONE short warm question per email, never re-ask what they answered. Record the name
in _system/identity.md, build the `self: true` person entity, keep README.md the dashboard. When
your acceptance test passes, write the file `.scaffolded` (content: today's date) — that releases
their meeting minutes — and say so in your final email. Plain text only. Their address: """

GROUP_KICKOFF = """[email-group-onboarding] You are setting up the GROUP workspace {group} over email
with its organizer. Read flows/shared.md. Ask what this group is for and who belongs (ONE question
per email); write CLAUDE.md, PURPOSE and README.md for it under kg-group/{group}/ in this
workspace (the shared-workspace store lands later — the content is what matters), and when settled
write the file `.scaffolded-group-{group}` (content: today's date) and say the group is ready.
Plain text only. Organizer: """

PROCESS_KICKOFF = """[post-meeting] The meeting (id {mid}) completed; final transcript below. Do ALL of:
1) write the meeting note at kg/entities/meeting/{date}-{native}.md — frontmatter, then sections
   Decided / Committed / Open, each item attributed, people as [[wikilinks]]; update the index;
2) update README.md as the dashboard;
3) end your reply with the note body EXACTLY as written, then a line '---', then 2-4 crisp action
   points. Your reply's text is emailed to the participants verbatim, so no meta-commentary.

TRANSCRIPT:
{transcript}"""


def build(reg: Registry, db) -> None:
    # ── shared small steps ────────────────────────────────────────────────────
    @reg.step
    def ensure_user(ctx: StepCtx):
        uid = ensure_platform_user(ctx.refs["organizer"])
        return Done({"uid": uid}, provider_ref=uid)

    @reg.step
    def rsvp_accept(ctx: StepCtx):
        mid = mx.send_rsvp_accept(ctx.refs["organizer"], ics_uid=ctx.refs["ics_uid"],
                                  start_epoch=ctx.refs["start"], title=ctx.refs["title"])
        return Done({"message_id": mid}, provider_ref=mid)

    @reg.step
    def ack_by_email(ctx: StepCtx):
        uid = ctx.prior["ensure_user"]["uid"]
        ready = scaffolded(uid)
        body = (f"Vexa accepted the invitation and joins {ctx.refs['url']} at "
                f"{time.strftime('%H:%M', time.localtime(ctx.refs['start']))}.")
        if not ready:
            body += ("\n\nOne thing before your minutes can flow: your workspace isn't set up yet — "
                     "answer the setup email that follows (it's a short conversation, not a form).")
        mid = mx.send(ctx.refs["organizer"], f"Vexa will join: {ctx.refs['title']}", body)
        # the ack is a thread anchor too: replying to the meeting confirmation is a conversation
        mx.register_thread(db, mid, uid, "main" if ready else "onboarding")
        return Done({"message_id": mid, "workspace_ready": ready}, provider_ref=mid)

    @reg.step
    def spawn_onboardings(ctx: StepCtx):
        uid = ctx.prior["ensure_user"]["uid"]
        spawned = {}
        if not scaffolded(uid):
            spawned["person"] = ctx.emit(ONB_PERSON.name, f"onbp-{ctx.refs['organizer']}",
                                         {"person": ctx.refs["organizer"], "uid": uid})
        g = ctx.refs.get("group")
        if g and ws_file(uid, f".scaffolded-group-{g}") is None:
            spawned["group"] = ctx.emit(ONB_GROUP.name, f"onbg-{g}",
                                        {"group": g, "organizer": ctx.refs["organizer"], "uid": uid})
        return Done(spawned)

    reg.step(mt.await_start)
    reg.step(mt.dispatch_bot)
    reg.step(mt.run_meeting)

    @reg.step
    def emit_completed(ctx: StepCtx):
        d = ctx.prior["dispatch_bot"]
        ctx.emit(COMPLETED.name, f"done-{d['meeting_id']}",
                 {**ctx.refs, "meeting_id": d["meeting_id"], "native": d["native"],
                  "transcript": ctx.prior["run_meeting"]["transcript"],
                  "uid": ctx.prior["ensure_user"]["uid"]})
        return Done({})

    # ── conversation machinery (dispatch/collect pairs — restart-proof) ───────
    def _conversation(prefix: str, session_of, kickoff_of, gate_of, subject_line):
        @reg.step
        def _open(ctx: StepCtx):
            uid = ctx.refs["uid"]
            ag.workspace_init(uid)
            base = ag.dispatch_turn(uid, session_of(ctx), kickoff_of(ctx))
            return Done({"baseline": base})
        _open.__name__ = f"open_{prefix}"
        reg.steps[f"open_{prefix}"] = reg.steps.pop("_open")

        @reg.step
        def _drive(ctx: StepCtx):
            uid, session = ctx.refs["uid"], session_of(ctx)
            if gate_of(ctx):
                return Done({"ready": True})
            sent = ctx.prior.get(f"open_{prefix}", {})
            base = ctx.scratch.get("baseline", sent.get("baseline", 0))
            reply = ag.collect_reply(uid, session, base)
            if reply is not None and ctx.scratch.get("emailed_at_len") != len(ag.history(uid, session)):
                mid = mx.send(ctx.refs.get("person") or ctx.refs["organizer"],
                              subject_line(ctx), reply,
                              in_reply_to=ctx.scratch.get("thread"))
                mx.register_thread(db, mid, uid, session)
                ctx.scratch["thread"] = ctx.scratch.get("thread") or mid
                ctx.scratch["emailed_at_len"] = len(ag.history(uid, session))
                ctx.scratch["last_mail_at"] = ctx.clock_now
            elif ctx.clock_now - ctx.scratch.get("last_mail_at", ctx.clock_now) > NUDGE_EVERY_S:
                mid = mx.send(ctx.refs.get("person") or ctx.refs["organizer"],
                              "Still there? " + subject_line(ctx),
                              "Reply whenever — your minutes wait for this thread.",
                              in_reply_to=ctx.scratch.get("thread"))
                ctx.scratch["last_mail_at"] = ctx.clock_now
            return Wait(seconds=10)
        _drive.__name__ = f"drive_{prefix}"
        reg.steps[f"drive_{prefix}"] = reg.steps.pop("_drive")

    _conversation("person",
                  session_of=lambda ctx: "onboarding",
                  kickoff_of=lambda ctx: ONBOARD_KICKOFF + ctx.refs["person"],
                  gate_of=lambda ctx: scaffolded(ctx.refs["uid"]),
                  subject_line=lambda ctx: "Getting you set up")
    _conversation("group",
                  session_of=lambda ctx: f"group-{ctx.refs['group']}",
                  kickoff_of=lambda ctx: GROUP_KICKOFF.format(group=ctx.refs["group"]) + ctx.refs["organizer"],
                  gate_of=lambda ctx: ws_file(ctx.refs["uid"], f".scaffolded-group-{ctx.refs['group']}") is not None,
                  subject_line=lambda ctx: f"Setting up #{ctx.refs['group']}")

    # ── post-meeting, gated ───────────────────────────────────────────────────
    @reg.step
    def require_workspace(ctx: StepCtx):
        uid = ctx.refs["uid"]
        ok = scaffolded(uid) and (not ctx.refs.get("group")
                                  or ws_file(uid, f".scaffolded-group-{ctx.refs['group']}") is not None)
        if ok:
            return Done({"ready": True})
        if ctx.clock_now - ctx.scratch.get("nudged_at", 0) > NUDGE_EVERY_S:
            mx.send(ctx.refs["organizer"], "Your minutes are waiting",
                    "The meeting is recorded. Finish the setup conversation and the minutes arrive "
                    "right after — just reply to that thread.")
            ctx.scratch["nudged_at"] = ctx.clock_now
        return Wait(seconds=60)

    @reg.step
    def process_meeting(ctx: StepCtx):
        uid = ctx.refs["uid"]
        if "baseline" not in ctx.scratch:
            ctx.scratch["shas"] = ag.commit_shas(uid)
            ctx.scratch["baseline"] = ag.dispatch_turn(
                uid, f"meet-{ctx.refs['meeting_id']}",
                PROCESS_KICKOFF.format(mid=ctx.refs["meeting_id"], native=ctx.refs["native"],
                                       date=time.strftime("%Y-%m-%d"),
                                       transcript=ctx.refs["transcript"] or "(no speech captured)"))
            return Wait(seconds=12)
        reply = ag.collect_reply(uid, f"meet-{ctx.refs['meeting_id']}", ctx.scratch["baseline"])
        sha, path = ag.latest_meeting_note(uid, ctx.scratch["shas"])
        if reply is not None and sha:
            return Done({"sha": sha, "note_path": path, "summary": reply[:6000]})
        if ctx.clock_now - ctx.scratch.get("t0", ctx.scratch.setdefault("t0", ctx.clock_now)) > 900:
            raise StepError("agent produced no note in 15min", retryable=False)
        return Wait(seconds=10)

    @reg.step
    def email_minutes(ctx: StepCtx):
        p = ctx.prior["process_meeting"]
        note = ws_file(ctx.refs["uid"], p["note_path"]) or p["summary"]
        body = (note + f"\n\n—\nRecorded by Vexa · commit {p['sha']}\n"
                "Reply to this email with corrections or questions — I'll update the workspace "
                "and answer here.")
        mid = mx.send(ctx.refs["organizer"], f"Minutes: {ctx.refs['title']}", body)
        mx.register_thread(db, mid, ctx.refs["uid"], f"meet-{ctx.refs['meeting_id']}")
        return Done({"message_id": mid}, provider_ref=mid)

    # ── the standing email conversation ───────────────────────────────────────
    @reg.step
    def feedback_turn(ctx: StepCtx):
        uid, session = ctx.refs["uid"], ctx.refs["session"]
        if "baseline" not in ctx.scratch:
            ctx.scratch["baseline"] = ag.dispatch_turn(
                uid, session,
                "[email-reply] The participant replied by email. Process it: update the workspace "
                "where it changes facts (feedback on minutes → amend the note; onboarding answers → "
                "continue the discovery loop and remember the .scaffolded acceptance). Then answer "
                "them — plain text, emailed verbatim.\n\nTHEIR EMAIL:\n" + ctx.refs["text"])
            return Wait(seconds=10)
        reply = ag.collect_reply(uid, session, ctx.scratch["baseline"])
        if reply is None:
            if ctx.clock_now - ctx.scratch.get("t0", ctx.scratch.setdefault("t0", ctx.clock_now)) > 600:
                raise StepError("agent silent for 10min", retryable=True)
            return Wait(seconds=8)
        return Done({"reply": reply[:6000]})

    @reg.step
    def email_reply(ctx: StepCtx):
        mid = mx.send(ctx.refs["from_addr"], "Re: " + (ctx.refs.get("subject") or "Vexa"),
                      ctx.prior["feedback_turn"]["reply"], in_reply_to=ctx.refs.get("orig_msgid"))
        mx.register_thread(db, mid, ctx.refs["uid"], ctx.refs["session"])
        return Done({"message_id": mid}, provider_ref=mid)

    s = reg.steps
    reg.flow(name="invite_intake", version=1, on=INVITE,
             steps=[s["ensure_user"], s["rsvp_accept"], s["ack_by_email"], s["spawn_onboardings"],
                    s["await_start"], s["dispatch_bot"], s["run_meeting"], s["emit_completed"]])
    reg.flow(name="onboard_person", version=1, on=ONB_PERSON,
             steps=[s["open_person"], s["drive_person"]])
    reg.flow(name="onboard_group", version=1, on=ONB_GROUP,
             steps=[s["open_group"], s["drive_group"]])
    reg.flow(name="post_meeting", version=1, on=COMPLETED,
             steps=[s["require_workspace"], s["process_meeting"], s["email_minutes"]])
    reg.flow(name="email_chat", version=1, on=MAIL_REPLY,
             steps=[s["feedback_turn"], s["email_reply"]])
