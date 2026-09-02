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

import time
from pathlib import Path

from flows import Done, Registry, StepCtx, StepError, Wait, EventType

from flows_steps import agent as ag
from flows_steps import emailx as mx          # thread bookkeeping + the iMIP calendar reply only
from flows_steps import meeting as mt
from flows_steps.common import (UI_URL, ensure_platform_user, scaffolded,
                                setting, ui_link, ws_file)
from flows_steps.notify import notify

INVITE = EventType("invite.received")
ONB_PERSON = EventType("onboarding.person.needed")
ONB_GROUP = EventType("onboarding.group.needed")
COMPLETED = EventType("meeting.completed")
UPCOMING = EventType("meeting.upcoming")
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
            # ONE agent turn produces everything, including the per-attendee follow-ups when the
            # personal variant is on. No per-attendee agent, no per-attendee session before a
            # click — the button composes the chat when it is pressed, as the organizer's does.
            if _followup_mode(ctx) == "personal":
                who = _attendees(ctx)
                if who:
                    kick += (
                        "\n\nALSO, in this same turn, write the file "
                        f"mail_outbox/attendees-{ctx.refs['meeting_id']}.md. For each address "
                        f"below write a section `## <address>` followed by at most three lines "
                        "addressed to that person: what THEY committed to, what was asked of "
                        "them, or what they asked — from the transcript, in their words where "
                        "possible. If the meeting held nothing for that person, write the "
                        "meeting's single decision instead. No greeting, no sign-off, no "
                        "meta-commentary.\n" + "\n".join(who))
            ctx.scratch["baseline"] = ag.dispatch_turn(
                uid, f"meet-{ctx.refs['meeting_id']}", kick)
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
            return Done({"sha": sha, "note_path": path, "summary": reply[:6000]})
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
        """
        import os
        box = os.environ.get("VEXA_MAIL_ADDR", "").strip()
        if not box:
            return ""
        return ("\nWant Vexa in a meeting of your own? Forward its calendar invite to "
                f"{box}.\n")

    # ── the attendee follow-up — the loop that spreads (PRD §16.1/§16.2) ─────────────────────
    def _followup_mode(ctx) -> str:
        """off | shared | personal.

        `shared` (default ON) is Marvin's own rule read across to SPI — creator-controlled
        sharing, default on, with a per-meeting opt-out (`refs.share is False`). Default OFF and
        this loop is dead on day one; that one value IS the coefficient.
        `personal` is the same single agent run writing a per-person line for each attendee.
        """
        if ctx.refs.get("share") is False:
            return "off"
        return str((ctx.flow.param("attendee_followup") if ctx.flow else None) or "shared")

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

        Two variants, both ONE agent turn per meeting:
          shared    the note's essentials, the same body to everyone (the cheap baseline)
          personal  the per-person block the same turn already wrote to
                    mail_outbox/attendees-<id>.md

        Reads: refs.{participants, organizer, title, meeting_id, share?} · Effect: N notifications
        Result: {sent, mode, skipped}."""
        mode = _followup_mode(ctx)
        who = _attendees(ctx)
        if mode == "off" or not who:
            return Done({"sent": 0, "mode": mode,
                         "skipped": "opted out" if mode == "off" else "no inside-domain attendee"})
        p = ctx.prior["process_meeting"]
        note = _readable(ws_file(ctx.refs["uid"], p["note_path"])
                         or p["summary"] or "")
        blocks = {}
        if mode == "personal":
            raw = ws_file(ctx.refs["uid"], f"mail_outbox/attendees-{ctx.refs['meeting_id']}.md")
            for chunk in (raw or "").split("## ")[1:]:
                head, _, rest = chunk.partition("\n")
                blocks[head.strip().lower()] = rest.strip()
        subject = f"{ctx.refs['title']} — what it means for you"
        # The meeting belongs to the ORGANISER. Without a capability the attendee's link resolves
        # to "no such meeting" and the agent greets them as a new user instead of telling them
        # what the meeting held — every attendee click landed on the wrong chat. One restricted
        # grant per attendee, redeemed by the terminal on arrival.
        row = mt.meeting_row(ctx.refs["uid"], ctx.refs.get("meeting_id"), ctx.refs.get("native"))
        platform = (row or {}).get("platform") or ctx.refs.get("platform") or "unknown"
        native = (row or {}).get("native_meeting_id") or ctx.refs.get("native") or ""
        sent = []
        for a in who:
            token = None
            if native:
                try:
                    token = mt.mint_transcript_share(ctx.refs["uid"], platform, native, a)
                except Exception:  # noqa: BLE001 — a mail with a weaker link beats no mail
                    token = None
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
            link = ui_link(ask=ask, meeting=ctx.refs["meeting_id"], tshare=token)
            body = blocks.get(a) if mode == "personal" else None
            if not body:
                body = note.strip()
            body = _provenance(ctx, ctx.refs["uid"], to_attendee=True) + body
            # PLACEMENT, not prose. The mailbox line existed and was still not landing: at
            # n=48 the top fixable blocker among people who CAN add the bot was `unclear_how` —
            # "nobody told me how to set this up in my dailies or who does it, and I don't have
            # time to figure it out before tomorrow" — from coordinators who had been sent the
            # instruction. It sat below the whole note, past the separator, where somebody with
            # two minutes between dailies never reaches. It now runs directly under the two
            # provenance lines, above the note. Same sentence, same placeholder wording; only
            # where it sits changed, and where it sits is mechanics.
            body = _provenance(ctx, ctx.refs["uid"], to_attendee=True) + _mailbox_line().lstrip("\n") \
                + "\n" + body[len(_provenance(ctx, ctx.refs["uid"], to_attendee=True)):]
            body += "\n\n—\nOpen it and ask anything about the meeting:"
            try:
                mid = notify(a, subject, body, link=link)
                sent.append(a)
            except Exception as e:  # noqa: BLE001 — one bad address never blocks the rest
                ctx.scratch.setdefault("failed", []).append(f"{a}: {type(e).__name__}")
        return Done({"sent": len(sent), "mode": mode, "to": sent,
                     "failed": ctx.scratch.get("failed", [])})

    # ── before the meeting ────────────────────────────────────────────────────
    @reg.step
    def prepare_meeting(ctx: StepCtx):
        """The front door of the loop whose back door is email_minutes: one short note asking
        whether they want to walk in ready, carrying `?ask=prep&meeting=<ref>`.

        Five lines, plain text, one link — a prepare mail that has to be read twice has already
        failed. Honours mail_prep exactly as email_minutes honours mail_minutes.
        Reads: refs.{organizer|person, title, start, uid?, meeting_id?, url?}
        Effect: one notification · Result: {message_id, meeting_ref}."""
        to = ctx.refs.get("person") or ctx.refs["organizer"]
        uid = str(ctx.refs.get("uid") or (ctx.prior.get("ensure_user") or {}).get("uid")
                  or ensure_platform_user(to))
        if not setting(uid, "mail_prep"):
            return Done({"skipped": "mail_prep is off for this person"})
        ref = str(ctx.refs.get("meeting_id") or "")
        if not ref and ctx.refs.get("url"):
            ref = mt.meeting_ref(uid, ctx.refs["url"])
        if not ref:
            raise StepError("nothing to link to — refs carry neither meeting_id nor url",
                            retryable=False)
        title = ctx.refs.get("title") or "your meeting"
        body = (f"{title} — {_their_clock(uid, ctx.refs['start'])}.\n"
                "Want to walk in ready? Open the chat and I'll pull together what we already know.")
        mid = notify(to, f"Prepare: {title}", body, link=ui_link(ask="prep", meeting=ref))
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
                    s["email_attendees"]])
    reg.flow(name="email_chat", version=1, on=MAIL_REPLY,
             steps=[s["feedback_turn"], s["email_reply"]])
