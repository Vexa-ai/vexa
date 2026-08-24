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
from pathlib import Path

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

def _repo_root() -> Path:
    # repo checkout: <root>/core/flows/src/flows_defs/production.py → parents[4];
    # the image is shallower (/app/src/flows_defs/…), so parents[4] may not exist
    p = Path(__file__).resolve()
    return p.parents[4] if len(p.parents) > 4 else p.parents[len(p.parents) - 1]


_SHOWCASE = next((c / "behavior" / "prompts" for c in
                  (Path("/"), Path("/app"), _repo_root())   # image bakes /behavior; checkout has <root>/behavior
                  if (c / "behavior" / "prompts").is_dir()),
                 _repo_root() / "behavior" / "prompts")


def _prompt(fname: str) -> str:
    """Behavior-domain prompt — machinery contains no prose, and the REAL voice is PROPRIETARY:
    resolution is (1) flow params override, (2) the PRIVATE behavior mount
    ($VEXA_BEHAVIOR_DIR/prompts/, deployed as a content tree like _global), (3) the in-repo
    showcase default (published to demonstrate capability, never the product's actual voice)."""
    import os
    private = os.environ.get("VEXA_BEHAVIOR_DIR")
    if private:
        f = Path(private) / "prompts" / fname
        if f.is_file():
            return f.read_text()
    return (_SHOWCASE / fname).read_text()


def prompt_for(ctx, fname: str, default: str) -> str:
    over = (ctx.flow.param("prompts") or {}) if ctx.flow else {}
    return over.get(fname, default)

ONBOARD_KICKOFF = _prompt("onboard-person.md")

GROUP_KICKOFF = _prompt("onboard-group.md")

PROCESS_KICKOFF = _prompt("process-meeting.md")


def build(reg: Registry, db) -> None:
    # ── shared small steps ────────────────────────────────────────────────────
    @reg.step
    def ensure_user(ctx: StepCtx):
        """Provision the platform user for the organizer (idempotent lookup-or-create).
        Reads: refs.organizer · Effect: admin-api user (+scoped token minted per later call)
        Result: {uid} — every later step's identity."""
        uid = ensure_platform_user(ctx.refs["organizer"])
        return Done({"uid": uid}, provider_ref=uid)

    @reg.step
    def rsvp_accept(ctx: StepCtx):
        """Accept the invitation IN THE ORGANIZER'S CALENDAR — iMIP METHOD:REPLY over SMTP;
        Google flips Vexa to "Yes" in the guest list. Reads: refs.{organizer,ics_uid,start,title}
        Effect: one calendar reply email · Result: {message_id}."""
        mid = mx.send_rsvp_accept(ctx.refs["organizer"], ics_uid=ctx.refs["ics_uid"],
                                  start_epoch=ctx.refs["start"], title=ctx.refs["title"])
        return Done({"message_id": mid}, provider_ref=mid)

    @reg.step
    def ack_by_email(ctx: StepCtx):
        """Acknowledge by email: when Vexa joins, plus the finalize-your-workspace ask when
        onboarding is pending. Registers the mail as a THREAD ANCHOR (replies become conversation).
        Reads: refs.{organizer,url,start,title} · Prior: ensure_user · Effect: one email
        Result: {message_id, workspace_ready}."""
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
        """EMIT onboarding facts when workspaces are missing: onboarding.person.needed for the
        organizer without `.scaffolded`; onboarding.group.needed when refs.group is set and the
        group marker is absent. Sub-flow composition: emits facts, never calls flows.
        Prior: ensure_user · Result: {person?, group?} (reactions created)."""
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

    reg.step(mt.check_platform)
    reg.step(mt.await_start)
    reg.step(mt.dispatch_bot)
    reg.step(mt.run_meeting)

    @reg.step
    def emit_completed(ctx: StepCtx):
        """EMIT meeting.completed carrying meeting identity + transcript — the fact the
        post-meeting flows react to. Prior: dispatch_bot, run_meeting."""
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
            """Open the onboarding conversation: seed the workspace, dispatch the agent kickoff
            (non-blocking — the freeze law). The agent's replies arrive via the FILE-OUTBOX
            contract; the human's via threaded email. Reads: refs.uid (+person/group)."""
            uid = ctx.refs["uid"]
            ag.workspace_init(uid)
            base = ag.dispatch_turn(uid, session_of(ctx), kickoff_of(ctx))
            return Done({"baseline": base})
        _open.__name__ = f"open_{prefix}"
        reg.steps[f"open_{prefix}"] = reg.steps.pop("_open")

        @reg.step
        def _drive(ctx: StepCtx):
            """Drive the conversation to the AGENT'S OWN ACCEPT: email each new outbox content
            (send-once registry), nudge on silence (params: nudge cadence), Done when the agent
            writes its acceptance marker (.scaffolded / group marker). Effect: emails."""
            uid, session = ctx.refs["uid"], session_of(ctx)
            if gate_of(ctx):
                return Done({"ready": True})
            reply, h = ag.collect_outbox(uid, session, ctx.scratch.get("sent_hash"))
            if reply is not None and db.execute(
                    "SELECT 1 FROM mail_outbox_sent WHERE subject_uid=:u AND session=:s AND hash=:h",
                    {"u": uid, "s": session, "h": h}):
                ctx.scratch["sent_hash"] = h                    # seen globally — don't resend
                reply = None
            if reply is not None:
                mid = mx.send(ctx.refs.get("person") or ctx.refs["organizer"],
                              subject_line(ctx), reply,
                              in_reply_to=ctx.scratch.get("thread"))
                mx.register_thread(db, mid, uid, session)
                ctx.scratch["thread"] = ctx.scratch.get("thread") or mid
                ctx.scratch["sent_hash"] = h
                db.execute("""INSERT INTO mail_outbox_sent (subject_uid, session, hash, sent_at)
                              VALUES (:u,:s,:h,:t) ON CONFLICT DO NOTHING""",
                           {"u": uid, "s": session, "h": h, "t": ctx.clock_now})
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
                  kickoff_of=lambda ctx: prompt_for(ctx, "onboard-person.md", ONBOARD_KICKOFF) + ctx.refs["person"],
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
        """THE QUEUE GATE: minutes wait for workspace readiness — `.scaffolded` for the owner
        (+ the group marker when refs.group). Not ready → nudge email on a cadence (params:
        nudge_every_s) then Wait(60); unbounded on purpose: late, never lost.
        Reads: refs.{uid,organizer,group?} · Effect: nudge emails · Result: {ready}."""
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
        """REAL agent turn on session meet-<id>: write the meeting note (Decided/Committed/
        Open, wikilinked) into the workspace and commit. Completion detected by a commit touching
        kg/entities/meeting/ (matched by PATH, never count). Params: style (rendering guidance).
        Reads: refs.{uid,meeting_id,native,transcript} · Effect: agent worker + git commit
        Result: {sha, note_path}."""
        uid = ctx.refs["uid"]
        if "baseline" not in ctx.scratch:
            ctx.scratch["shas"] = ag.commit_shas(uid)
            ctx.scratch["baseline"] = ag.dispatch_turn(
                uid, f"meet-{ctx.refs['meeting_id']}",
                prompt_for(ctx, "process-meeting.md", PROCESS_KICKOFF).format(mid=ctx.refs["meeting_id"], native=ctx.refs["native"],
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
        """Send the committed note VERBATIM in the email body (UI-less law) + the feedback ask;
        registers the thread → meet-<id> session. Cannot run before the commit: its input IS
        process_meeting's receipt. Reads: refs.{uid,organizer,title} · Effect: one email."""
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
        """One conversation turn: hand the inbound email to the session's agent (workspace
        updated where facts changed), collect the reply via the FILE-OUTBOX contract
        (mail_outbox/<session>.md, content-hash), coalesced across sibling reactions.
        Reads: refs.{uid,session,text} · Effect: agent worker turn · Result: {reply}."""
        uid, session = ctx.refs["uid"], ctx.refs["session"]
        if "dispatched" not in ctx.scratch:
            ctx.scratch["prev_hash"] = ag.collect_outbox(uid, session, None)[1]
            ag.dispatch_turn(
                uid, session,
                "[email-reply] The participant replied by email. Process it: update the workspace "
                "where it changes facts (feedback on minutes → amend the note; onboarding answers → "
                "continue the discovery loop and remember the .scaffolded acceptance). Then answer "
                f"them. DELIVERY CONTRACT: write your answer to the file mail_outbox/{session}.md "
                "(overwrite fully) — that file is emailed verbatim, plain text."
                "\n\nTHEIR EMAIL:\n" + ctx.refs["text"])
            ctx.scratch["dispatched"] = True
            return Wait(seconds=10)
        reply, h = ag.collect_outbox(uid, session, ctx.scratch.get("prev_hash"))
        if reply is not None:
            already = db.execute("SELECT 1 FROM mail_outbox_sent WHERE subject_uid=:u AND session=:s AND hash=:h",
                                 {"u": uid, "s": session, "h": h})
            if already:
                return Done({"reply": "", "coalesced": True})   # another reaction already mailed this content
            ctx.scratch["out_hash"] = h
        if reply is None:
            if ctx.clock_now - ctx.scratch.get("t0", ctx.scratch.setdefault("t0", ctx.clock_now)) > 600:
                raise StepError("agent silent for 10min", retryable=True)
            return Wait(seconds=8)
        return Done({"reply": reply[:6000]})

    @reg.step
    def email_reply(ctx: StepCtx):
        """Mail the agent's reply on the same thread; register Message-ID; record the content
        hash in mail_outbox_sent (send-once across reactions and restarts).
        Prior: feedback_turn · Effect: one email."""
        ft = ctx.prior["feedback_turn"]
        if not ft.get("reply"):
            return Done({"coalesced": True})                    # content already mailed by a sibling
        mid = mx.send(ctx.refs["from_addr"], "Re: " + (ctx.refs.get("subject") or "Vexa"),
                      ft["reply"], in_reply_to=ctx.refs.get("orig_msgid"))
        mx.register_thread(db, mid, ctx.refs["uid"], ctx.refs["session"])
        db.execute("""INSERT INTO mail_outbox_sent (subject_uid, session, hash, sent_at)
                      VALUES (:u,:s,:h,:t) ON CONFLICT DO NOTHING""",
                   {"u": ctx.refs["uid"], "s": ctx.refs["session"],
                    "h": ctx.scratch.get("out_hash", ""), "t": ctx.clock_now})
        return Done({"message_id": mid}, provider_ref=mid)

    s = reg.steps
    reg.flow(name="invite_intake", version=1, on=INVITE,
             # check_platform sits BEFORE rsvp_accept: an RSVP is a promise to attend, so the
             # refusal has to land before the calendar flips Vexa to "Yes".
             steps=[s["ensure_user"], s["check_platform"], s["rsvp_accept"], s["ack_by_email"],
                    s["spawn_onboardings"],
                    s["await_start"], s["dispatch_bot"], s["run_meeting"], s["emit_completed"]])
    reg.flow(name="onboard_person", version=1, on=ONB_PERSON,
             steps=[s["open_person"], s["drive_person"]])
    reg.flow(name="onboard_group", version=1, on=ONB_GROUP,
             steps=[s["open_group"], s["drive_group"]])
    reg.flow(name="post_meeting", version=1, on=COMPLETED,
             steps=[s["require_workspace"], s["process_meeting"], s["email_minutes"]])
    reg.flow(name="email_chat", version=1, on=MAIL_REPLY,
             steps=[s["feedback_turn"], s["email_reply"]])
