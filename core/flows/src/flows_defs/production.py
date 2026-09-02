"""PRODUCTION flows (founder spec 2026-08-23, evening scope):

  1. invite_intake      — info@vexa.ai invited → user ensured → iMIP ACCEPT in the calendar →
                          ack email (+ finalize-workspace ask if needed) → personal onboarding
                          spawned · #group:name → group onboarding spawned → bot at start−2min →
                          meeting → completed fact
  2. onboard_person     — a REAL agent conversation over email (threaded) until the AGENT
                          writes `.scaffolded`; silence is chased with nudges
  3. onboard_group      — same conversation pattern for the #group workspace, chased by email
  4. post_meeting       — gated on `.scaffolded` → agent processes through the workspace →
                          summary + action points VERBATIM by email, asking for feedback, AND
                          one link into the minutes terminal already primed on this meeting
  5. email_chat         — every threaded reply becomes an agent turn (feedback processed, the
                          workspace updated) and the agent's answer goes back by email: the
                          standing conversation
  6. meeting_prep       — a NEW upcoming meeting → one short "prepare?" note carrying the same
                          shape of link, primed on the prep ask instead of the review ask

The 2026-08-23 line "UI-less: email is the entire surface" is retired. Email is the DOOR: every
mail out of here carries at most one link, into a chat that is already about the thing the mail
is about, and the sign-in hop preserves it. What travels the wire is a NOTIFICATION (flows_steps
.notify) — the recipes no longer name SMTP.

Laws (from the live witness): steps never sleep · all state in refs/receipts · replies by thread."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from flows import Done, Registry, StepCtx, StepError, Wait, EventType

from flows_steps import agent as ag
from flows_steps import emailx as mx          # thread bookkeeping + the iMIP calendar reply only
from flows_steps import meeting as mt
from flows_steps import mailtext
from flows_steps.common import (UI_URL, ensure_platform_user, platform_user_id, scaffolded,
                                setting, ui_link, ws_file)
from flows_steps.notify import notify

INVITE = EventType("invite.received")
ONB_PERSON = EventType("onboarding.person.needed")
ONB_GROUP = EventType("onboarding.group.needed")
COMPLETED = EventType("meeting.completed")
UPCOMING = EventType("meeting.upcoming")
MAIL_REPLY = EventType("mail.reply")

NUDGE_EVERY_S = 15 * 60

logger = logging.getLogger("flows.production")

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

    def _their_clock(uid, epoch):
        """A time in the person's zone, with the zone attached — never the server's clock."""
        import datetime
        tz = setting(uid, "timezone")
        if tz:
            try:
                import zoneinfo
                t = datetime.datetime.fromtimestamp(epoch, zoneinfo.ZoneInfo(tz))
                return t.strftime("%H:%M") + " " + (t.tzname() or tz)
            except Exception:  # noqa: BLE001
                pass
        return datetime.datetime.fromtimestamp(
            epoch, datetime.timezone.utc).strftime("%H:%M") + " UTC"

    @reg.step
    def rsvp_accept(ctx: StepCtx):
        """Accept the invitation IN THE ORGANIZER'S CALENDAR — iMIP METHOD:REPLY over SMTP;
        Google flips Vexa to "Yes" in the guest list. Reads: refs.{organizer,ics_uid,start,title}
        Effect: one calendar reply email · Result: {message_id}."""
        uid = (ctx.prior.get("ensure_user") or {}).get("uid")
        if uid and not setting(uid, "mail_rsvp"):
            return Done({"skipped": "mail_rsvp is off for this person"})
        mid = mx.send_rsvp_accept(ctx.refs["organizer"], ics_uid=ctx.refs["ics_uid"],
                                  start_epoch=ctx.refs["start"], title=ctx.refs["title"])
        return Done({"message_id": mid}, provider_ref=mid)

    @reg.step
    def ack_by_email(ctx: StepCtx):
        """Acknowledge by email: when Vexa joins, plus the finalize-your-workspace ask when
        onboarding is pending. Registers the mail as a THREAD ANCHOR (replies become conversation).
        Reads: refs.{organizer,url,start,title} · Prior: ensure_user · Effect: one notification
        Result: {message_id, workspace_ready}."""
        uid = ctx.prior["ensure_user"]["uid"]
        if not setting(uid, "mail_join"):
            return Done({"skipped": "mail_join is off for this person"})
        ready = scaffolded(uid)
        body = (f"Vexa accepted the invitation and joins {ctx.refs['url']} at "
                f"{_their_clock(uid, ctx.refs['start'])}.")
        if not ready:
            body += ("\n\nOne thing before your minutes can flow: your workspace isn't set up yet — "
                     "answer the setup email that follows (it's a short conversation, not a form).")
        mid = notify(ctx.refs["organizer"], f"Vexa will join: {ctx.refs['title']}", body)
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
        # PROVENANCE SURVIVES DERIVATION. The other emits spread `{**ctx.refs, …}` and so carry
        # `admitted_by` forward without thinking about it; this one builds a fresh dict, so the
        # onboarding reaction was the one place in the chain that could not say who caused it.
        # "Who invited the mailbox to that meeting" has to stay answerable one hop from the
        # answer, not only at the hop that happened to keep the parent's refs.
        by = ctx.refs.get("admitted_by")
        if not scaffolded(uid):
            spawned["person"] = ctx.emit(ONB_PERSON.name, f"onbp-{ctx.refs['organizer']}",
                                         {"person": ctx.refs["organizer"], "uid": uid,
                                          "admitted_by": by})
        g = ctx.refs.get("group")
        if g and ws_file(uid, f".scaffolded-group-{g}") is None:
            spawned["group"] = ctx.emit(ONB_GROUP.name, f"onbg-{g}",
                                        {"group": g, "organizer": ctx.refs["organizer"],
                                         "uid": uid, "admitted_by": by})
        return Done(spawned)

    @reg.step
    def emit_prep(ctx: StepCtx):
        """EMIT meeting.upcoming — the fact the prepare flow reacts to.

        THE ADMIT: on this deployment an invite IS the meeting-created event. Nothing else
        publishes one — mailbox.py admits only invite.received and mail.reply, and a meeting made
        any other way (the terminal, the control MCP's bot_schedule, calendar sync) reaches the
        platform's meetings table without telling flows. So the fact is emitted from inside
        invite_intake, before await_start parks: a second producer can admit the same event type
        later without touching this step. Prior: ensure_user."""
        ctx.emit(UPCOMING.name, f"prep-{ctx.refs['ics_uid']}",
                 {**ctx.refs, "uid": ctx.prior["ensure_user"]["uid"]})
        return Done({})

    reg.step(mt.await_start)
    reg.step(mt.dispatch_bot)
    reg.step(mt.run_meeting)

    @reg.step
    def emit_completed(ctx: StepCtx):
        """EMIT meeting.completed carrying IDENTITY ONLY — the fact the post-meeting flows react
        to. Prior: dispatch_bot, run_meeting.

        The transcript used to ride inside this event, truncated to 8,000 characters to fit. That
        made the event a second home for a fact the transcription domain already owns, and the cap
        was the product's ceiling: on an hour-long meeting the agent saw about the first twelve
        minutes, so its notes were well-formed and nearly content-free (measured — the mechanical
        score said 0.94 while the judge said 7/100, and both were right).

        Identity travels; the words stay where they live. The agent reads them itself over the MCP
        with its delegation token, in full."""
        d = ctx.prior["dispatch_bot"]
        refs = {k: v for k, v in ctx.refs.items() if k != "transcript"}
        ctx.emit(COMPLETED.name, f"done-{d['meeting_id']}",
                 {**refs, "meeting_id": d["meeting_id"], "native": d["native"],
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
                mid = notify(ctx.refs.get("person") or ctx.refs["organizer"],
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
                mid = notify(ctx.refs.get("person") or ctx.refs["organizer"],
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
        Reads: refs.{uid,organizer,group?} · Effect: nudge notifications · Result: {ready}."""
        uid = ctx.refs["uid"]
        ok = scaffolded(uid) and (not ctx.refs.get("group")
                                  or ws_file(uid, f".scaffolded-group-{ctx.refs['group']}") is not None)
        if ok:
            return Done({"ready": True})
        if ctx.clock_now - ctx.scratch.get("nudged_at", 0) > NUDGE_EVERY_S:
            notify(ctx.refs["organizer"], "Your minutes are waiting",
                   "The meeting is recorded. Finish the setup conversation and the minutes arrive "
                   "right after — just reply to that thread.")
            ctx.scratch["nudged_at"] = ctx.clock_now
        return Wait(seconds=60)

    @reg.step
    def process_meeting(ctx: StepCtx):
        """REAL agent turn on session meet-<id>: write the meeting note (Decided/Committed/
        Open, wikilinked) into the workspace and commit. Completion detected by a commit touching
        kg/entities/meeting/ (matched by PATH, never count). Params: style (rendering guidance).
        Reads: refs.{uid,meeting_id,native} · Effect: agent worker + git commit
        Result: {sha, note_path}."""
        uid = ctx.refs["uid"]
        if "baseline" not in ctx.scratch:
            ctx.scratch["shas"] = ag.commit_shas(uid)
            kick = prompt_for(ctx, "process-meeting.md", PROCESS_KICKOFF).format(
                mid=ctx.refs["meeting_id"], native=ctx.refs["native"],
                date=_meeting_stamp(ctx, uid))
            # ONE SHARED REPORT, NOT A PERSONALISED ONE (founder, 2026-09-02). What was here
            # asked the same turn for a `mail_outbox/attendees-<id>.md` of `## <address>` sections
            # — a paragraph per person — and the mail step then decided who got which. That whole
            # mechanism is GONE: one meeting produces one report, everybody receives it, and the
            # personal half of the conversation happens in the chat AFTER the click, where the
            # person is present and can be asked.
            #
            # The attribution rule is the one thing the shared report has to be told, because the
            # post-meeting turn now runs with READ ACCESS to the attendees' workspaces: a person's
            # workspace may inform what the report says, and may never be quoted into it. A report
            # that goes to everyone in the room is not a place where one person's private notes
            # can appear.
            # WHOSE WORKSPACES THIS TURN MAY READ: the people who SPOKE, most-speaking first,
            # capped by `room_read_max`. Computed here because flows is where the transcript is
            # reachable, and sent as a PROPOSAL — agent-api verifies it against the meeting's real
            # participants and mounts the intersection read-only, so this can only ever narrow the
            # set. Nothing here mounts anything, and a selection that cannot be computed is an
            # empty list, which means the turn reads nobody.
            room_read = mt.speaking_order(uid, ctx.refs["meeting_id"],
                                          ctx.refs.get("participants") or [],
                                          cap=_room_read_max(ctx))
            ctx.scratch["room_read"] = room_read
            kick += (
                "\n\nTHE REPORT IS SHARED. One report for this meeting, sent unchanged to "
                "everybody who was in the room — the organiser and every attendee read the same "
                "words. Do not write a section per person, do not address anyone individually, "
                "and do not write anything that only one reader is supposed to see.\n\n"
                "MEETING-RELEVANT FACTS ONLY, ATTRIBUTED — a person's workspace informs the "
                "report, it is never quoted into it. "
                + (("You have READ-ONLY access to the workspaces of the people who spoke in this "
                    "meeting (" + ", ".join(room_read) + "). Use them to understand what was said "
                    "and to attribute it correctly, and never copy a line, a note or a phrase out "
                    "of one into this report. ")
                   if room_read else "")
                + "Everything in the report was said, decided, committed or asked IN THIS "
                "ROOM.\n\n"
                "Anything person-centric happens when they click the link in the mail, not here."
            )
            ctx.scratch["baseline"] = ag.dispatch_turn(
                uid, f"meet-{ctx.refs['meeting_id']}", kick, room_read=room_read)
            return Wait(seconds=12)
        reply = ag.collect_reply(uid, f"meet-{ctx.refs['meeting_id']}", ctx.scratch["baseline"])
        sha, path = ag.latest_meeting_note(uid, ctx.scratch["shas"])
        if reply is not None and sha:
            # THE GROUNDING GATE. Removing the transcript from the event made the note depend on
            # the agent CHOOSING to fetch it, and measured on Haiku it chooses to about half the
            # time — and when it does not, it writes a confident note anyway, from the title and
            # the prompt. That is strictly worse than the truncated copy it replaced: a shallow
            # note is visibly shallow, a fabricated one is not.
            #
            # An instruction is not a gate. So the step checks: does the note share any six-word
            # run with the actual transcript? If not, the agent is told exactly that, once, and
            # asked again. If it still cannot ground it, the reaction FAILS LOUDLY rather than
            # emailing minutes nobody can trace to the meeting.
            note = ws_file(uid, path)
            if not mt.grounded_in(note or reply, mt.transcript_text(uid, ctx.refs["meeting_id"])):
                if not ctx.scratch.get("regrounded"):
                    ctx.scratch["regrounded"] = True
                    ctx.scratch["shas"] = ag.commit_shas(uid)
                    ctx.scratch["baseline"] = ag.dispatch_turn(
                        uid, f"meet-{ctx.refs['meeting_id']}",
                        "STOP. The note you just wrote contains nothing that appears in the "
                        f"meeting. You did not read it. Call mcp__vexa__meeting_transcript with "
                        f"meeting_id={ctx.refs['meeting_id']} and tail=0 NOW, read every segment, "
                        "then rewrite the note from what it returns — quoting one verbatim "
                        "sentence with its speaker. If you cannot call that tool, say so and "
                        "write nothing.")
                    return Wait(seconds=12)
                raise StepError(
                    "the note is not grounded in the transcript — the agent did not read the "
                    "meeting, twice. Refusing to email minutes that cannot be traced to it.",
                    retryable=False)
            return Done({"sha": sha, "note_path": path, "summary": reply[:6000],
                         # WHERE the note is, so nothing downstream has to guess. "" means the
                         # ORGANISER'S own workspace, which is the only place this step looks:
                         # `ag.latest_meeting_note(uid, …)` reads `/api/workspace/git` with no
                         # slug, i.e. the caller's primary.
                         #
                         # FINDING (2026-09-02, unresolved here on purpose): PRD §5.2 says a GROUP
                         # meeting's note belongs in the GROUP's workspace, and this step does not
                         # do that — it dispatches as the organiser and detects the commit in the
                         # organiser's own repo, so `refs.group` changes nothing about where the
                         # note lands. Rather than have the pointer in `drop_to_attendees` assert
                         # a group path the note is not at, the pointer reads THIS field, so it is
                         # correct today and becomes correct for groups the moment whoever fixes
                         # §5.2 sets it. One source of truth for one fact.
                         "note_workspace": "",
                         "room_read": ctx.scratch.get("room_read", [])})
        if reply is not None:
            # THE TURN ENDED AND PRODUCED NO NOTE. Silence and finishing-empty are different
            # facts, and this step used to treat them the same: wait, and after fifteen minutes
            # say "agent produced no note in 15min" — a sentence that names the symptom and
            # discards the cause. The agent had, in one measured case, already explained itself in
            # its first thirty seconds ("the tool appears in the deferred MCP tools list, but I
            # don't have a direct function invocation"), and the step spent the next fifteen
            # minutes not reading it. A stalled fixture cost 34 minutes of a replay that way.
            #
            # A collected reply means the turn is OVER. Ask once more, naming what happened, and
            # then fail with the agent's OWN LAST WORDS in the reason, because that is the only
            # part of this that tells anyone what to fix.
            if not ctx.scratch.get("re_asked_note"):
                ctx.scratch["re_asked_note"] = True
                ctx.scratch["shas"] = ag.commit_shas(uid)
                ctx.scratch["baseline"] = ag.dispatch_turn(
                    uid, f"meet-{ctx.refs['meeting_id']}",
                    "Your last turn ended without writing the meeting note. Do it now: call "
                    f"mcp__vexa__meeting_transcript with meeting_id={ctx.refs['meeting_id']} and "
                    "tail=0, read every segment, then write the note. If that tool will not run "
                    "for you, reply with the exact error and write nothing — do not summarise "
                    "from the title.")
                return Wait(seconds=12)
            raise StepError(
                "the agent's turn ended twice with no note. Its last words: "
                + " ".join(reply.split())[:280], retryable=False)
        # Still running: the long wait stays, because a turn that is genuinely working is allowed
        # to take its time. It just no longer covers a turn that has already given up.
        if ctx.clock_now - ctx.scratch.get("t0", ctx.scratch.setdefault("t0", ctx.clock_now)) > 900:
            raise StepError("the agent turn never finished (no reply after 15min)", retryable=False)
        return Wait(seconds=10)

    @reg.step
    def email_minutes(ctx: StepCtx):
        """Send the committed note VERBATIM in the body + the feedback ask + ONE link into the
        minutes terminal, already primed on this meeting. Cannot run before the commit: its input
        IS process_meeting's receipt.

        The link is `?ask=minutes-review&meeting=<row-id>` on VEXA_UI_URL. Both params compose and
        both survive the sign-in hop, so a signed-out reader clicks once, gets a magic link, and
        lands in a chat that already holds the meeting — the reply-by-email door stays open beside
        it, unchanged. The preset body is admin-owned (`_global/asks/minutes-review.md`) and
        substitutes {{meeting}}: the URL never carries prompt text, so nobody who can send mail can
        drive somebody else's agent.

        Reads: refs.{uid,organizer,title,meeting_id} · Effect: one notification."""
        if not setting(ctx.refs["uid"], "mail_minutes"):
            return Done({"skipped": "mail_minutes is off for this person"})
        p = ctx.prior["process_meeting"]
        note = _readable(ws_file(ctx.refs["uid"], p["note_path"])
                         or p["summary"])
        body = (_provenance(ctx, ctx.refs["uid"], to_attendee=False)
                + note + f"\n\n—\nRecorded by Vexa · commit {p['sha']}\n"
                "Reply to this email with corrections or questions — I'll update the workspace "
                "and answer here. Or open it and talk it through:")
        link = ui_link(ask="minutes-review", meeting=ctx.refs["meeting_id"])
        mid = notify(ctx.refs["organizer"], f"Minutes: {ctx.refs['title']}", body, link=link)
        mx.register_thread(db, mid, ctx.refs["uid"], f"meet-{ctx.refs['meeting_id']}")
        return Done({"message_id": mid, "link": link}, provider_ref=mid)



    def _readable(note: str) -> str:
        """The note as a PERSON meets it in a mail.

        The note is a WORKSPACE artifact: YAML frontmatter, wikilinks, workspace-relative
        links. Mailing it verbatim put `type: meeting / id: ... / tags: [...]` at the top of
        the first thing an attendee ever sees from us, and shipped
        `[Recording](/?meeting=27)` — a RELATIVE url, which is a dead link in every mail
        client there is. Neither is a rendering preference; both are the workspace leaking
        through the product surface, and the attendee mail is where it costs the most.
        """
        import re
        body = note or ""
        if body.lstrip().startswith("---"):
            rest = body.lstrip()[3:]
            end = rest.find("\n---")
            if end != -1:
                body = rest[end + 4:]
        body = re.sub(r"\[([^\]]+)\]\((/[^)]*)\)",
                      lambda m: m.group(1) + ": " + UI_URL + m.group(2), body)
        body = re.sub(r"\[\[([^\]]+)\]\]", r"\1", body)
        return body.strip()


    def _meeting_stamp(ctx, uid) -> str:
        """The stamp that goes into the note's FILENAME — the meeting's own occurrence, never
        the day a worker happened to process it.

        `date=time.strftime("%Y-%m-%d")` was the processing date, and the note path is
        `kg/entities/meeting/{date}-{native}.md`. A recurring meeting keeps ONE
        native_meeting_id across its occurrences, so any two occurrences written on the same
        day landed on the same path: the second silently overwrote the first, or the agent saw
        the mismatch, refused to write, and process_meeting timed out after 15 minutes with
        "agent produced no note". Replay makes this the normal case rather than the edge one —
        ten recorded meetings replayed this afternoon are ten occurrences processed today.

        THE RULE, stated because a filename that is wrong by one day collides with the next day's
        occurrence exactly the way the processing-date bug did:

          the instant   refs.start, else the meeting row's start_time, else its created_at
          the clock     the ORGANIZER'S timezone when we know it, else UTC — never the server's
          the shape     %Y-%m-%d-%H%M, so two occurrences on ONE day are still two files

        The server's clock was the quiet defect. Every branch here rendered in UTC or the person's
        zone except the no-start fallback, which used `time.strftime` — local time on whichever
        machine happened to run the worker. A meeting near midnight then landed on a different day
        depending on where the process was, which is the one thing a filename must never do.
        """
        import datetime
        start = ctx.refs.get("start")
        if not start:
            start = mt.meeting_start(uid, ctx.refs.get("meeting_id"), ctx.refs.get("native"))
        zone = datetime.timezone.utc
        tz = setting(uid, "timezone")
        if tz:
            try:
                import zoneinfo
                zone = zoneinfo.ZoneInfo(tz)
            except Exception:  # noqa: BLE001 — an unknown zone name falls back to UTC, never local
                zone = datetime.timezone.utc
        if not start:
            # Still deterministic and still not the server's clock: a meeting with no knowable
            # start is stamped in the same zone as one that has it.
            return datetime.datetime.now(zone).strftime("%Y-%m-%d-%H%M")
        return datetime.datetime.fromtimestamp(float(start), zone).strftime("%Y-%m-%d-%H%M")


    def _provenance(ctx, uid, to_attendee: bool) -> str:
        """The two lines that go ABOVE every minutes / follow-up mail.

        From the personas' own stated reasons for ignoring these mails, in revolution 1: an
        engineer asked where the audio and transcript live and whether there is an API, and got
        nothing; a coordinator could not work out why a message from a domain they did not
        recognise was telling them about a meeting. Neither objection is about the minutes. Both
        are answered in two lines, and both have to come FIRST, because a reader who does not
        know why they are being written to does not reach the content.

        Line 1 — why this arrived: the meeting they were in, when, and who had Vexa in the room.
        Line 2 — where the words live, who can read them, and how to stop. `data_statement` is a
        DEPLOYMENT fact (a studio running this on its own hardware says so in its own words), so
        it is a flow param with an env fallback, never a sentence baked into the machinery.
        """
        import datetime
        import os
        title = ctx.refs.get("title") or "your meeting"
        organizer = ctx.refs.get("organizer") or "the organiser"
        when = ""
        start = ctx.refs.get("start")
        if start:
            try:
                when = " on " + datetime.datetime.fromtimestamp(
                    float(start), datetime.timezone.utc).strftime("%-d %B")
            except Exception:  # noqa: BLE001
                when = ""
        who = (ctx.flow.param("data_statement") if ctx.flow else None) or \
            os.environ.get("VEXA_FLOWS_DATA_STATEMENT") or \
            "Vexa runs on this organisation's own servers; the recording and transcript stay there."
        if to_attendee:
            first = f"You were in {title}{when}. {organizer} had Vexa in the room, so these are the notes."
        else:
            first = f"You had Vexa in {title}{when}."
        # The opt-out sentence that used to close this line is GONE. Measured: it lifted opening
        # 61.9% -> 84.1% and quadrupled explicit opt-out, 6.3% -> 27.0%, while action stayed
        # inside noise — it converted silent ignoring into deliberate leaving. Whether to put an
        # unsubscribe in every mail is a founder call, and the evidence does not support this
        # wording. Opt-out lives in the chat and in settings instead.
        second = f"{who} These notes are visible to the people who were in the meeting."
        return first + "\n" + second + "\n\n"


    def _mailbox_line() -> str:
        """The one line that makes a second invite possible, naming the mailbox THIS deployment
        actually watches.

        Two things were wrong before it. The mail never mentioned that Vexa could be in a meeting
        of the reader's own, so the second-invite funnel had nothing at stage 1 — measured 0/4
        offered in the mail. And the chat preset that does make the offer had the address baked
        into it as a literal, which is only correct for the deployment it was written on: the
        mailbox is `VEXA_MAIL_ADDR`, a deployment fact, and a preset in `_global` cannot read it.
        The flow can, so the address travels in the artifact that knows it.

        PLACEHOLDER WORDING — the founder has not chosen these words.

        CURRENTLY UNREFERENCED (2026-09-02). The attendee mail was its only caller, and its head
        is now the founder's own file (`deploy/dogfood/mail/attendee-head.md`) — this sentence is
        not in it. Kept, not deleted, because it is the only place the deployment's own mailbox
        address is turned into prose: putting the offer back is one `{{mailbox}}` token in that
        file plus one entry in the `values` dict handed to `mailtext.render`, and deleting this
        would lose the env lookup that makes it correct on a deployment that is not ours.
        """
        import os
        box = os.environ.get("VEXA_MAIL_ADDR", "").strip()
        if not box:
            return ""
        return ("\nWant Vexa in a meeting of your own? Forward its calendar invite to "
                f"{box}.\n")


    def _meeting_date(ctx, uid) -> str:
        """`{{date}}` — the day the MEETING happened, in the organiser's zone.

        Derived from `_meeting_stamp`, NOT `_their_clock`. `_their_clock` renders a clock time
        ("14:30 CEST") from an epoch the caller must already hold, and it has no answer at all
        when refs carry no `start`; the head needs a DATE and has to survive that case.
        `_meeting_stamp` already owns exactly the rules this needs — refs.start, else the meeting
        row's start_time, else its created_at; the organiser's timezone, else UTC, never the
        server's clock — so this reuses it and only reshapes `%Y-%m-%d-%H%M` into prose.
        Reimplementing that fallback chain here is how the two would drift apart."""
        import datetime
        stamp = _meeting_stamp(ctx, uid)
        try:
            return datetime.datetime.strptime(stamp[:10], "%Y-%m-%d").strftime("%-d %B %Y")
        except Exception:  # noqa: BLE001
            return stamp[:10]


    # ── the attendee follow-up — the loop that spreads (PRD §16.1/§16.2) ─────────────────────
    def _followup_on(ctx) -> bool:
        """Does the attendee fan-out run for this meeting at all?

        `shared` (default ON) is Marvin's own rule read across to SPI — creator-controlled
        sharing, default on, with a per-meeting opt-out (`refs.share is False`). Default OFF and
        this loop is dead on day one; that one value IS the coefficient.

        THE PERSONAL/SHARED AXIS IS GONE (founder, 2026-09-02): one meeting produces one report
        and everybody in the room receives it unchanged, so there is nothing left to choose
        between. `attendee_followup` survives as the ON/OFF SWITCH it also was — a deployment
        that has it set to `off` keeps meaning that, and losing the kill switch to a refactor
        would be a silent re-enabling of a fan-out somebody turned off on purpose. Any other
        value, including the historical `personal` and `shared`, simply means on.

        REMOVED WITH THE AXIS: `attendee_silent_policy` and `attendee_personal_max`. Both existed
        only to decide what a person the meeting held nothing FOR should receive, and a shared
        report holds something for everyone who was in the room. They are gone rather than left
        inert: an inert param a deployment can still set is a control that silently does nothing,
        which is worse than one that is not there.
        """
        if ctx.refs.get("share") is False:
            return False
        v = (ctx.flow.param("attendee_followup") if ctx.flow else None)
        if v is None:
            return True                 # UNSET IS ON. Spelled out because `str(None)` is the
        return str(v).strip().lower() not in ("off", "none", "false", "0")   # string "none".

    def _room_read_max(ctx) -> int:
        """`room_read_max` — DEFAULT 12. How many of a meeting's speakers the post-meeting turn
        may have read-only workspace mounts for.

        Founder, 2026-09-02: *"need to make sure agent will not die if it has 200 folders in it."*
        The cap is on MOUNTS, not on people: everybody on the invite still gets the mail and the
        drop entity, because those are a write per person and cost nothing per head. Reading is
        what does not scale.

        A non-numeric or non-positive value is the default, never an error — and zero would be
        indistinguishable from "unset" while meaning the opposite, so it is not a way to say
        "mount nobody"; `attendee_domains` and the verification on agent-api's side own that."""
        raw = (ctx.flow.param("room_read_max") if ctx.flow else None)
        try:
            n = int(raw)
        except (TypeError, ValueError):
            return 12
        return n if n > 0 else 12

    def _attendees(ctx) -> list:
        """Inside-domain attendees, minus the organizer. PRD §16.2: outside the domain, NEVER —
        so an unset allow-list means the organizer's own domain, not everyone."""
        import os
        org = (ctx.refs.get("organizer") or "").lower()
        raw = ctx.refs.get("participants") or []
        allow = (ctx.flow.param("attendee_domains") if ctx.flow else None) or \
            [d for d in os.environ.get("VEXA_FLOWS_ATTENDEE_DOMAINS", "").split(",") if d] or \
            ([org.split("@")[-1]] if "@" in org else [])
        allow = {d.strip().lower().lstrip("@") for d in allow if d}
        out = []
        for a in raw:
            a = str(a).strip().lower()
            if "@" not in a or a == org or a in out:
                continue
            if a.split("@")[-1] in allow:
                out.append(a)
        return out

    @reg.step
    def email_attendees(ctx: StepCtx):
        """Every inside-domain ATTENDEE gets the follow-up plus ONE button into a chat the click
        composes. Cannot run before the note: its input is process_meeting's receipt.

        THE SHAPE, in order: HEAD → the shared report → one gap line → one button. Nothing else.
        The head AND the subject are one `mailtext.render("attendee-head", …)` — the live text is
        `_global/mail/attendee-head.md`, re-read every send, falling back to the identical baked
        default; the gap line and the button are `notify.compose`'s, so the step never writes the
        url itself.

        ONE REPORT, THE SAME MAIL TO EVERYONE (founder, 2026-09-02). Every inside-domain attendee
        receives byte-identical words — the same report the organiser gets from `email_minutes` —
        and the ONLY thing that differs per person is the share token in their own button, which
        has to differ because a forwarded link must grant its new reader nothing. There is no
        per-person section, no `## _decision`, no silent-attendee policy and no room-size cap: the
        `mail_outbox/attendees-<id>.md` mechanism those needed is gone from `process_meeting`.
        Personalisation happens in the chat AFTER the click, where the person is there to be asked.

        Reads: refs.{participants, organizer, title, meeting_id, share?} · Effect: N notifications
        Result: {sent, followup, skipped, drops, failed}.

        `drops` is what `drop_to_attendees` runs on: one entry per person the mail ACTUALLY went
        to, carrying the exact link they were given, so the next step neither recomputes the
        fan-out nor mints a second share capability per attendee. It does mean the receipt holds
        those links, tokens included: see the step below."""
        on = _followup_on(ctx)
        who = _attendees(ctx)
        if not on or not who:
            return Done({"sent": 0, "followup": "on" if on else "off", "to": [], "drops": [],
                         "skipped": "no inside-domain attendee" if on else "opted out"})
        p = ctx.prior["process_meeting"]
        # THE ONE ARTEFACT. `_readable` is what turns the workspace note into something a person
        # meets in a mail (frontmatter off, wikilinks flattened, relative links absolutised); it is
        # the same string `email_minutes` puts in front of the organiser, which is what "the same
        # report" means operationally rather than as an intention.
        report = _readable(ws_file(ctx.refs["uid"], p["note_path"]) or p["summary"] or "").strip()
        # The meeting belongs to the ORGANISER. Without a capability the attendee's link resolves
        # to "no such meeting" and the agent greets them as a new user instead of telling them
        # what the meeting held — every attendee click landed on the wrong chat. One restricted
        # grant per attendee, redeemed by the terminal on arrival.
        # THE GATE. A touch that cannot work is not sent — the same doctrine as the grounding gate
        # in process_meeting, and it is here because the opposite shipped: the mint used to return
        # None on any non-2xx (not raise), the `except Exception` below it therefore never fired,
        # and the comment "a mail with a weaker link beats no mail" described a mail whose link was
        # not weaker but BROKEN. On 2026-09-02 meeting 97 went out to its attendees with no token
        # at all; every one of them clicked into a chat that answered "no meeting with id 97 on my
        # side". A mail nobody sent costs one missing follow-up. A mail whose only button lands the
        # reader in a chat that denies the meeting exists costs the relationship the mail was for.
        row = mt.meeting_row(ctx.refs["uid"], ctx.refs.get("meeting_id"), ctx.refs.get("native"))
        # By ROW id, never by (platform, native): row 97 was platform='unknown' with an empty
        # native, so no pair addressed it and the mint could only ever 404. Prefer the row the
        # platform just handed us over the ref, which may still be a native id from meeting_ref().
        mid = (row or {}).get("id") if isinstance(row, dict) else None
        if mid is None:
            mid = ctx.refs.get("meeting_id")
        if mid is None or not str(mid).isdigit():
            raise StepError(
                f"cannot mail {len(who)} attendee(s): this meeting has no row id to mint a share "
                f"against (got {mid!r} from the row and refs). Every attendee link would resolve "
                "to a meeting the reader cannot see.", retryable=False)
        # THE HEAD AND THE SUBJECT COME OUT OF ONE READER. `mailtext.render` is the contract
        # `deploy/dogfood/mail/README.md` states for this whole directory: the live text is
        # `_global/mail/attendee-head.md` — git-backed, admin-writable, mounted into every worker
        # — falling back to the identical baked default in `flows_steps/mailtext.py`, and the
        # `subject:` / `---` header is PARSED rather than mailed.
        #
        # This step used to run its own reader (`mail_template` + `_fill`) against the REPO path,
        # which is not what a send reads: an admin's live edit was ignored, and because that reader
        # did not know about the header the body began with the literal line `subject: … ---`. Two
        # readers for one directory is the defect; a third would be worse, so there is now one.
        # `{{company}}` and `{{service}}` are `render`'s own — no caller can forget them or spell
        # the product differently — and an unknown token is left STANDING on purpose.
        try:
            subject, head = mailtext.render("attendee-head", ctx.refs["uid"], {
                "organizer": ctx.refs.get("organizer") or "the organiser",
                "meeting": ctx.refs.get("title") or "your meeting",
                "date": _meeting_date(ctx, ctx.refs["uid"]),
            })
        except KeyError as e:
            # No baked default AND no `_global` override. It cannot happen while `mailtext.DEFAULTS`
            # carries `attendee-head`, and it must not become a silent half-mail if it ever does:
            # a fan-out with no introduction is exactly the stranger-facing failure the head exists
            # to prevent. Not retryable — a missing template is not a passing condition.
            raise StepError(
                f"cannot mail {len(who)} attendee(s) for meeting {mid}: there is no "
                f"'attendee-head' mail template — neither a baked default nor "
                f"`_global/mail/attendee-head.md` ({e}).", retryable=False) from e
        if not subject:
            # `mailtext._split` returns "" when a template lost its `subject:` line, and says the
            # caller has to supply one — a mail with an empty subject line reads as spam. The
            # meeting's own title is the honest last resort, and the loss is logged because it
            # means somebody edited the header out of the live file.
            logger.warning("the attendee-head template carries no `subject:` line — falling back "
                           "to the meeting title")
            subject = ctx.refs.get("title") or "Your meeting"
        # ONE BODY, BUILT ONCE, FOR EVERYBODY. Nothing inside the loop touches it.
        body = head + "\n\n" + report
        # Durable across retries: a StepError below re-runs this step, and an attendee already
        # mailed must not be mailed twice. ctx.scratch is persisted after every step.
        sent = list(ctx.scratch.setdefault("sent", []))
        # What each person was actually told, recorded as they are told it — in scratch, so a
        # retry that skips an already-mailed attendee still carries their entry forward.
        drops = list(ctx.scratch.setdefault("drops", []))
        for a in who:
            if a in sent:
                continue
            try:
                token = mt.mint_transcript_share(ctx.refs["uid"], mid, a)
            except mt.ShareMintError as e:
                pending = [x for x in who if x not in sent]
                raise StepError(
                    f"HELD the attendee fan-out for meeting {mid}: no share capability could be "
                    f"minted for {a} (HTTP {e.status} — {e.detail}). "
                    f"Mailed: {', '.join(sent) or 'nobody'}. "
                    f"NOT mailed: {', '.join(pending)}. "
                    "Not sending: a mail whose only button opens a chat that cannot see the "
                    "meeting is worse than no mail.",
                    retryable=e.retryable) from e
            # Which preset the button composes. `minutes-review-invite` is `minutes-review`
            # plus the SECOND ASK — the offer that Vexa can be in the meetings this person runs,
            # and the instruction to ACT on a yes in the same turn (bot_schedule when a url and
            # time are known, else the one-line forward). The offer is the whole second-invite
            # funnel: measured at revolution 3 it was never made, in the mail or in the chat, so
            # nobody could ask for it and it could never happen.
            # A PARAM so the offer is one value to turn off, and so the founder's wording can
            # replace the placeholder without touching a step.
            ask = str((ctx.flow.param("attendee_ask") if ctx.flow else None)
                      or "minutes-review-invite")
            # `mid`, not refs["meeting_id"]: the token was minted against the ROW, so the link
            # must name that same row. refs may still carry a native id from meeting_ref().
            link = ui_link(ask=ask, meeting=mid, tshare=token)
            # THE WHOLE MAIL: template head, one blank line, the shared report. The gap line and
            # the button after it are `notify.compose`'s ("body\n\n<url>\n") — the step does not
            # write the url, which is why the link is asserted on the call, not on prose.
            #
            # `body` is built OUTSIDE this loop on purpose: it does not depend on `a`, and one
            # string built once is the cheapest possible proof that everybody got the same words.
            #
            # What is GONE, deliberately: the `_provenance(...)` + `_mailbox_line()` preamble the
            # head replaces (it was spliced in twice — the second splice sliced the first back
            # off, which is how the double-splice went unnoticed), and the "—\nOpen it and ask
            # anything about the meeting:" trailer. Four elements were approved; this is four.
            try:
                notify(a, subject, body, link=link)
                sent.append(a)
                ctx.scratch["sent"] = sent
                drops.append({"to": a, "link": link})
                ctx.scratch["drops"] = drops
            except Exception as e:  # noqa: BLE001 — one bad address never blocks the rest
                ctx.scratch.setdefault("failed", []).append(f"{a}: {type(e).__name__}")
        return Done({"sent": len(sent), "followup": "on", "to": sent, "meeting_id": mid,
                     "drops": drops,               # what drop_to_attendees writes, per person
                     "failed": ctx.scratch.get("failed", [])})

    # ── the drop — one meeting entity into each attendee's own workspace (PRD decision 20) ──
    def _slug(text: str, cap: int = 60) -> str:
        """A meeting title as a filename fragment. Lowercase, one `-` per run of anything that is
        not a letter or a digit, capped, and never empty.

        The character class is an ALLOW-list on purpose: a title is attacker-adjacent text (it
        comes off a calendar invite anybody in the room can edit), so `/`, `..`, a leading dot, a
        NUL and every other separator are gone by construction rather than by a blacklist somebody
        has to keep complete. The cap keeps `<date>-<slug>.md` inside every filesystem's name
        limit."""
        import re as _re
        out = _re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:cap].rstrip("-")
        return out or "meeting"

    def _yaml(value: str) -> str:
        """One frontmatter scalar, always double-quoted. A meeting title legitimately contains
        `:`, `#`, `[`, quotes and emoji; unquoted, any of them turns the entity's own frontmatter
        into something a parser reads differently from what we wrote."""
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

    def _drop_entity(*, title, day, date_prose, organizer, attendee, where, note_path,
                     link) -> str:
        """THE SAME entity for everybody who was in the room: a real KG entity in the shape
        `kg/templates/meeting.md` defines, carrying the meeting's title, date and organiser and a
        POINTER to the canonical note — never a copy of it, and no personal line.

        One meeting has one record. This file says which meeting, when, whose Vexa was in the
        room, and exactly where the full note lives; a second copy of the note in every attendee's
        workspace would be five versions of one truth the moment the organiser corrects theirs.

        The ONE thing that differs per attendee is the link, because it carries that person's own
        restricted share token — a forwarded link must grant its new reader nothing."""
        return "\n".join([
            "---",
            "type: meeting",
            f"id: {day}-{_slug(title)}",
            f"title: {_yaml(title)}",
            f"date: {day}",
            f"organizer: {_yaml(organizer)}",
            f"participants: [{_yaml(organizer)}, {_yaml(attendee)}]",
            "tags: [vexa-attendee-drop]",
            "---",
            "",
            f"# {title}",
            "",
            f"{date_prose} — {organizer} had Vexa in the room.",
            "",
            "## The record",
            "",
            "This entity is a pointer, not a copy — the meeting has one note and it is not here.",
            (f"It lives in {where} at `{note_path}`." if note_path
             else f"It lives in {where}."),
            "",
            f"Open the meeting: {link}",
            "",
        ])

    def _index_entry(current: str, title: str, filename: str, day: str) -> str:
        """`kg/entities/meeting/index.md` with this meeting listed once.

        Returns the file as it should be; the caller writes only if that differs from what is
        there, so a re-run appends nothing. The seed's `_No entities yet…` placeholder is REPLACED
        rather than left standing above the first real row — it is the index saying it is empty,
        and it stops being true here."""
        line = f"- [{title}]({filename}) — {day}"
        if current is None:
            return ("# meeting\n\nMeetings — one file per meeting at "
                    "`meeting/<yyyy-mm-dd-slug>.md`.\n\n" + line + "\n")
        if f"]({filename})" in current:
            return current
        kept = [ln for ln in current.rstrip("\n").splitlines()
                if not ln.strip().startswith("_No entities yet")]
        while kept and not kept[-1].strip():
            kept.pop()
        return "\n".join(kept) + "\n\n" + line + "\n" if kept else line + "\n"

    def _write_if_changed(their_uid: str, path: str, content: str) -> bool:
        """Write only when the bytes differ. This is the ACROSS-RUN half of idempotence: scratch
        remembers who is done inside one run, but a worker restart loses scratch, and a re-run
        that rewrites an identical file still produces a second commit in somebody's history."""
        if ws_file(their_uid, path) == content:
            return False
        ag.workspace_write(their_uid, path, content)
        return True

    @reg.step
    def drop_to_attendees(ctx: StepCtx):
        """One meeting entity into EACH attendee's OWN workspace. PLAIN CODE — no agent turn, no
        LLM (founder ruling, PRD decision 20): a fan-out across a room is one HTTP write per
        person, cannot hallucinate, and costs nothing per head.

        Runs after `email_attendees` and only for the people that step ACTUALLY mailed — its input
        is that step's `drops` payload, which carries the exact link each of them was given.
        Nothing is recomputed and no second share capability is minted: two mechanisms deciding
        what one person was told is how they come to disagree.

        PER ATTENDEE, three effects and no others:
          1. their platform user (`ensure_platform_user`) and their workspace
             (`POST /api/workspace/init` AS THEM — the same seeding the click does). Nothing else
             is built: no chat, no session, no scaffolding.
          2. `kg/entities/meeting/<date>-<slug>.md` — the meeting's title, date and organiser and
             a POINTER to the canonical note (the organiser's workspace path plus the
             `?meeting=<row>` link carrying their own share token). Never a copy of the note, and
             NO personal line: everybody in the room gets the same entity, and the only thing that
             differs is the share token inside their own link.
          3. `kg/entities/meeting/index.md` gains one line, once.
        Every write goes through `PUT /api/workspace/file`, which commits — so each drop lands in
        their workspace history rather than as an untracked file.

        IT READS NO PRIVATE WORKSPACE. The only paths it reads in anybody's workspace are the two
        it is itself the author of — the entity above and that index — and it reads them for
        exactly one reason: so a second run writes nothing instead of a second entity, a second
        index line and a second commit. What it reads is compared to what it was about to write
        and then dropped: it is never returned, never logged, never shown to another person, and
        never mixed into anyone else's file. No transcript, no note, no settings, no chat, and
        nothing belonging to one attendee reaches another.

        IDEMPOTENT per (meeting, attendee) twice over, because the two halves fail differently:
        `ctx.scratch` skips people already done inside this run (a `StepError` re-runs the whole
        step), and each write is a content-compare on a stable path, which is what survives a
        worker restart that loses scratch entirely.

        FAILURE POLICY: one attendee's drop failing must never cost the others theirs, so each is
        attempted in its own try and the failures are collected into the result — a partial drop
        is a fact an operator can see and re-run. The step only fails when EVERY drop failed,
        which is not one person's bad state but the agent-api being unreachable; retryable, since
        every write above is safe to repeat.

        Prior: email_attendees{drops}, process_meeting{note_path} · Effect: N workspace writes
        Result: {dropped, to, failed, entity}."""
        prior = ctx.prior.get("email_attendees") or {}
        drops = prior.get("drops") or []
        if not drops:
            return Done({"dropped": 0, "to": [], "failed": [],
                         "skipped": "the follow-up mailed nobody, so there is nothing to drop"})
        uid = ctx.refs["uid"]
        title = ctx.refs.get("title") or "your meeting"
        organizer = ctx.refs.get("organizer") or "the organiser"
        day = _meeting_stamp(ctx, uid)[:10]          # the MEETING's day, in the organiser's zone
        date_prose = _meeting_date(ctx, uid)
        filename = f"{day}-{_slug(title)}.md"
        entity_path = f"kg/entities/meeting/{filename}"
        index_path = "kg/entities/meeting/index.md"
        pm = ctx.prior.get("process_meeting") or {}
        note_path = pm.get("note_path") or ""
        # WHERE THE CANONICAL NOTE ACTUALLY IS. `process_meeting` reports it rather than this step
        # inferring it: PRD §5.2 puts a group meeting's note in the GROUP's workspace and everyone
        # else's in the ORGANISER'S, and a pointer that asserts one while the note sits in the
        # other is worse than no pointer — the reader follows it, finds nothing, and stops
        # believing the entity. See the FINDING on `note_workspace` in that step.
        slug = str(pm.get("note_workspace") or "").strip()
        where = f"the #{slug} workspace" if slug else f"{organizer}'s workspace"
        done = list(ctx.scratch.setdefault("dropped", []))
        failed = list(ctx.scratch.setdefault("drop_failed", []))
        for d in drops:
            a = str((d or {}).get("to") or "").strip()
            if not a or a in done:
                continue
            try:
                their_uid = ensure_platform_user(a)
                ag.workspace_init(their_uid)
                _write_if_changed(their_uid, entity_path, _drop_entity(
                    title=title, day=day, date_prose=date_prose, organizer=organizer,
                    attendee=a, where=where, note_path=note_path,
                    link=d.get("link") or ""))
                _write_if_changed(their_uid, index_path, _index_entry(
                    ws_file(their_uid, index_path), title, filename, day))
                done.append(a)
                failed = [f for f in failed if not f.startswith(a + ":")]
            except Exception as e:  # noqa: BLE001 — one attendee never costs the rest theirs
                failed = [f for f in failed if not f.startswith(a + ":")]
                failed.append(f"{a}: {type(e).__name__}: {e}"[:240])
            ctx.scratch["dropped"] = done
            ctx.scratch["drop_failed"] = failed
        if done:
            return Done({"dropped": len(done), "to": done, "failed": failed,
                         "entity": entity_path, "meeting_id": prior.get("meeting_id")})
        raise StepError(
            f"every attendee drop failed for meeting {prior.get('meeting_id')} "
            f"({len(drops)} attendee(s), all mailed already): " + " · ".join(failed),
            retryable=True)

    # ── before the meeting ────────────────────────────────────────────────────
    @reg.step
    def prepare_meeting(ctx: StepCtx):
        """The front door of the loop whose back door is email_minutes: one short note asking
        whether they want to walk in ready, carrying `?ask=prep&meeting=<ref>`.

        A TEMPLATE — substitutions only, no agent turn — read from `_global/mail/prepare.md` so the
        wording is a file edit rather than a rebuild. Five lines, plain text, one link: a prepare
        mail that has to be read twice has already failed. Honours mail_prep exactly as
        email_minutes honours mail_minutes.

        IT NEVER GOES TO A STRANGER. Founder, 2026-09-02, on a pre-meeting fan-out across a room:
        *"I am afraid this will not work for a 50 attendee meeting."* Before the meeting there is
        nothing yet to justify a mail to somebody who has never heard of us — and the recipient of
        this one is the organiser, or a person who is already a user. A stranger meets Vexa AFTER
        their meeting, in the attendee follow-up, which is why that mail carries the introduction
        and this one does not. Relatedly: this mail must never claim a workspace was started for
        anyone. Nothing is built for a person who has not clicked.

        Reads: refs.{organizer|person, title, start, uid?, meeting_id?, url?}
        Effect: one notification · Result: {message_id, meeting_ref}."""
        to = ctx.refs.get("person") or ctx.refs["organizer"]
        # `person` is set when this fires for somebody other than the organiser. Only mail them if
        # they ALREADY have an account: ensure_platform_user would create one, and a prepare mail
        # is not a good enough reason to mint an identity for a person who has not asked for it.
        if ctx.refs.get("person") and ctx.refs["person"] != ctx.refs.get("organizer"):
            existing = platform_user_id(to)
            if not existing:
                return Done({"skipped": "not a user yet — a stranger meets Vexa after the meeting, "
                                        "not before it", "to": to})
            uid = str(existing)
        else:
            uid = str(ctx.refs.get("uid") or (ctx.prior.get("ensure_user") or {}).get("uid")
                      or ensure_platform_user(to))
        if not setting(uid, "mail_prep"):
            return Done({"skipped": "mail_prep is off for this person"})
        title = ctx.refs.get("title") or "your meeting"
        ref = str(ctx.refs.get("meeting_id") or "")
        if not ref and ctx.refs.get("url"):
            # PLAN it, do not merely address it. The link used to carry the native id because the
            # row is minted at dispatch — so the prep chat opened on a Zoom number, held nothing
            # under it, and reached for the only meeting it could find. dispatch_bot claims this
            # same row at start-2min, so nothing downstream forks.
            ref = mt.ensure_meeting_row(uid, ctx.refs["url"], ctx.refs.get("title"),
                                        ctx.refs.get("start"))
        if not ref:
            raise StepError("nothing to link to — refs carry neither meeting_id nor url",
                            retryable=False)
        subject, body = mailtext.render("prepare", uid, {
            "title": title, "when": _their_clock(uid, ctx.refs["start"]),
            "organizer": ctx.refs.get("organizer") or "",
        })
        mid = notify(to, subject or f"Prepare: {title}", body,
                     link=ui_link(ask="prep", meeting=ref))
        mx.register_thread(db, mid, uid, f"meet-{ref}")
        return Done({"message_id": mid, "meeting_ref": ref}, provider_ref=mid)

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
        Prior: feedback_turn · Effect: one notification."""
        ft = ctx.prior["feedback_turn"]
        if not ft.get("reply"):
            return Done({"coalesced": True})                    # content already mailed by a sibling
        mid = notify(ctx.refs["from_addr"], "Re: " + (ctx.refs.get("subject") or "Vexa"),
                     ft["reply"], in_reply_to=ctx.refs.get("orig_msgid"))
        mx.register_thread(db, mid, ctx.refs["uid"], ctx.refs["session"])
        db.execute("""INSERT INTO mail_outbox_sent (subject_uid, session, hash, sent_at)
                      VALUES (:u,:s,:h,:t) ON CONFLICT DO NOTHING""",
                   {"u": ctx.refs["uid"], "s": ctx.refs["session"],
                    "h": ctx.scratch.get("out_hash", ""), "t": ctx.clock_now})
        return Done({"message_id": mid}, provider_ref=mid)

    s = reg.steps
    reg.flow(name="invite_intake", version=1, on=INVITE,
             steps=[s["ensure_user"], s["rsvp_accept"], s["ack_by_email"], s["spawn_onboardings"],
                    s["emit_prep"],
                    s["await_start"], s["dispatch_bot"], s["run_meeting"], s["emit_completed"]])
    reg.flow(name="onboard_person", version=1, on=ONB_PERSON,
             steps=[s["open_person"], s["drive_person"]])
    reg.flow(name="onboard_group", version=1, on=ONB_GROUP,
             steps=[s["open_group"], s["drive_group"]])
    reg.flow(name="meeting_prep", version=1, on=UPCOMING,
             steps=[s["prepare_meeting"]])
    reg.flow(name="post_meeting", version=1, on=COMPLETED,
             steps=[s["require_workspace"], s["process_meeting"], s["email_minutes"],
                    s["email_attendees"], s["drop_to_attendees"]])
    reg.flow(name="email_chat", version=1, on=MAIL_REPLY,
             steps=[s["feedback_turn"], s["email_reply"]])
