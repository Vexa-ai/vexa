"""PRODUCTION flows (founder spec 2026-08-23, evening scope):

  1. invite_intake      — info@vexa.ai invited → user ensured → iMIP ACCEPT in the calendar →
                          ack email → prepare mail (organizer) → bot at start−2min → meeting →
                          completed fact. THREE TOUCHES AND NO OTHERS (decision 29).
  2. post_meeting       — the agent processes the meeting → report VERBATIM by email, asking for
                          feedback, AND one link into the minutes terminal already primed on this
                          meeting → the record dropped onto every desk in the room

DECISION 29 (founder, 2026-09-02) RETIRED TWO FLOWS AND ONE GATE. `onboard_person` and
`onboard_group` — agent conversations conducted over email until the agent wrote `.scaffolded` —
are gone, and with them `post_meeting`'s readiness gate. The mail that ended them said:

    "The meeting is recorded. Finish the setup conversation and the minutes arrive right after —
     just reply to that thread."

    → "no we do not want that."

A person's meeting is not a reward for completing our setup. The minutes are the FIRST thing they
see from us, and holding them hostage to a form inverts the product: it makes the machine's
readiness the customer's problem, and it does so at the exact moment they are most likely to care.
So `process_meeting` now runs on whatever desks exist — an unscaffolded desk is simply empty to
read, which costs the report some context and costs the person nothing — and the drop lands on it
regardless (decision 22a). `.scaffolded` survives as a harmless marker; it gates nothing.

Group setup moves into the chat behind a `group-setup` scaffold, where a person is present to be
asked, rather than into an email thread that chases them.
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
from flows_steps.common import (UI_URL, ensure_platform_user, mint_scaffold, platform_user_id,
                                scaffolded, setting, ws_file)
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
    """The LIVE kick for one step, in the order the rest of this file already claims: the flow's
    own `prompts` param, then the admin's `_global/prompts/<fname>`, then the baked default.

    THE MIDDLE ONE DID NOT EXIST (R-B18). This function read the flow param and nothing else,
    while `_shared_report_rules` asserted three screens down that "`prompt_for` reads a live
    `_global` override before the baked file" and the decision-22 detector told the operator to go
    and check "the LIVE kick (`_global` override, else `behavior/prompts/process-meeting.md`)".
    An admin who edited that file changed nothing, and the one instruction an operator is given
    when the detector fires pointed at a path the code never opened — which is worse than a
    missing feature, because it sends the person debugging a failure to the wrong file.

    `default` is the BAKED prompt, and it is baked at IMPORT (`PROCESS_KICKOFF = _prompt(...)`),
    which is where the "hot reload" half of the claim came from. The `_global` read below is the
    hot half and it is per-call, exactly as `mailtext.render` reads `_global/mail/<name>.md` on
    every send — the same contract, the same directory, one screen apart, and now the same shape.

    FAILS SOFT, twice over. An override that cannot be read (agent-api down, no uid in refs) and
    an override that is EMPTY or whitespace both fall through to the baked text. An admin who
    cleared the file by accident did not mean "dispatch a turn with no instructions"; `mailtext`
    learned the same lesson and its comment says so."""
    over = (ctx.flow.param("prompts") or {}) if ctx.flow else {}
    if fname in over:
        return over[fname]
    uid = str((getattr(ctx, "refs", None) or {}).get("uid") or "")
    if uid:
        try:
            live = ws_file(uid, f"prompts/{fname}", "_global")
        except Exception:  # noqa: BLE001 — a prompt we cannot fetch is not a reason to fail a turn
            live = None
        if (live or "").strip():
            return live
    return default


ONBOARD_KICKOFF = _prompt("onboard-person.md")

GROUP_KICKOFF = _prompt("onboard-group.md")

PROCESS_KICKOFF = _prompt("process-meeting.md")

# ── THE NOTE-PATH RECIPE ──────────────────────────────────────────────────────
# These three are MODULE-LEVEL and not `build()` closures, for one reason: this is the only
# description anywhere of where a meeting's record lives, and a recipe nothing can call directly
# is a recipe nothing can test directly. Its tests used to reach it by dispatching a whole
# `process_meeting` turn and reading the path back out of the PROMPT — which meant they were
# pinning the kick's spelling of the path, not the writer's, and the two had drifted apart
# without a single test going red (F55/F58, 2026-09-02).
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
    # COMPUTED ONCE PER REACTION, then stashed. Three moments ask for this stamp — the scaffold's
    # mint (`_scaffold_refs`), the drop's own `day`, and `_note_path` inside the drop — and two of
    # its three inputs can CHANGE between them: `mt.meeting_start` is an HTTP call that can fail
    # and later succeed, and the last fallback below is the wall clock, which moves. A run that
    # minted the mail's link at 19:47 and wrote the desk file at 19:52 named two different files;
    # the Minutes tab opened the one nothing wrote and read "No page here yet". That is F58, and
    # it re-opened on this second route after the `[:10]` slice was fixed on the first.
    #
    # `ctx.scratch` is the right home rather than a module global: it is per-reaction, it is
    # persisted, and it therefore survives the worker restart between `email_minutes` and
    # `drop_to_attendees` that a module global would not. Keyed by `uid` because the zone is that
    # person's. `getattr` because unit tests hand this function a ctx that is only `refs`.
    scratch = getattr(ctx, "scratch", None)
    key = f"_meeting_stamp:{uid}"
    if isinstance(scratch, dict) and scratch.get(key):
        return str(scratch[key])
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
        stamp = datetime.datetime.now(zone).strftime("%Y-%m-%d-%H%M")
    else:
        stamp = datetime.datetime.fromtimestamp(float(start), zone).strftime("%Y-%m-%d-%H%M")
    if isinstance(scratch, dict):
        scratch[key] = stamp
    return stamp

# HOW MANY TIMES THE ATTENDEE FAN-OUT RETRIES AN UNREACHABLE ADDRESS before it goes on without
# them. The step's own ceiling, deliberately not the engine's `MAX_ATTEMPTS`: what is being bounded
# here is one person's mail server, not the health of the reaction, and the two must be able to
# move independently. Below the ceiling a failure is retryable (a transient SMTP 421 is the case
# this exists for); at it the step COMPLETES with the address named in `failed`, because the steps
# after it — the desk drop that reaches the whole room — must not be held hostage by one bad
# address in a twenty-person invite.
ATTENDEE_MAIL_ATTEMPTS = 3


def _note_path(ctx, uid, title) -> str:
    """THE ONE RECIPE for where a meeting's record lands on a desk:
    `kg/entities/meeting/<meeting-day>-<title-slug>.md`.

    It exists because the recipe had TWO implementations in two languages. This one — inlined
    in `drop_to_attendees`, which actually WRITES the file — and a second in the terminal's
    `roomView.ts`, which pointed the Minutes tab at `kg/entities/meeting/<native>.md`. They
    never agreed, so the tab resolved to a file nothing writes and every reader saw "No page
    here yet" forever, on a meeting whose report had been written, mailed and dropped.

    The client is now TOLD the path (`refs.note_path` on the scaffold) instead of deriving it.
    A path is not a thing two services can each be trusted to spell — the day comes from the
    organiser's zone and the slug from an allow-list, and neither is guessable from outside."""
    # THE WHOLE STAMP, not its first ten characters. `_meeting_stamp` renders
    # `%Y-%m-%d-%H%M` and says why in its own docstring: "so two occurrences on ONE day are
    # still two files". `drop_to_attendees` was slicing that back down to `%Y-%m-%d` before
    # building the filename, which re-created the exact collision the stamp was written to
    # prevent — a recurring meeting keeps ONE title and ONE native across occurrences, so the
    # afternoon's record silently overwrote the morning's on every desk in the room. Nothing
    # failed; the morning simply stopped existing. (F58, 2026-09-02.)
    return f"kg/entities/meeting/{_meeting_stamp(ctx, uid)}-{_slug(str(title or 'meeting'))}.md"


def build(reg: Registry, db) -> None:
    # ── shared small steps ────────────────────────────────────────────────────
    @reg.step
    def ensure_user(ctx: StepCtx):
        """Provision the platform user for the organizer (idempotent lookup-or-create).
        Reads: refs.organizer · Effect: admin-api user (+scoped token minted per later call)
        Result: {uid} — every later step's identity.

        ⚠ IT REFUSES A NON-ADDRESS, and the reason is a real account: on 2026-09-02 this step
        created user 131 with the email `20260902t183213z` — an invite's own DTSTAMP, handed to it
        by an ICS parser that had matched the word "organizer" inside the UID line. The parse is
        fixed (`mailbox.parse_ics` anchors its property patterns), and this is the second lock,
        because this step is the LAST place that can tell: everything after it works with a uid and
        has no way to know the account behind it is a timestamp.

        A refusal here is not retryable — the refs are frozen at admission, so the same malformed
        value would arrive on every attempt — and it must be loud: an account minted from a parse
        artefact is invisible until somebody reads the user table, which is how this one was found.
        """
        who = str(ctx.refs.get("organizer") or "").strip()
        # The shape only — never a domain allow-list, which is a deployment's business and not
        # this step's. `a@b.c` is the whole test: one @, something either side, a dot in the host.
        local, _, host = who.rpartition("@")
        if not local or "." not in host or " " in who:
            raise StepError(
                f"the organizer on this invite is not an email address ({who[:80]!r}) — refusing "
                f"to create an account for it. A value like this comes from a parse, not from a "
                f"person, and every step after this one only sees the uid.",
                retryable=False)
        uid = ensure_platform_user(who)
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

    def _scaffold_refs(ctx, uid) -> dict:
        """THE FACTS A SCAFFOLD CARRIES — what the invite already knew, and nothing derived.

        Every line here is something the agent otherwise has to go and find, and on a small model
        "otherwise" often means "not at all": the prepare opening that named a meeting by its Zoom
        id and then said it held nothing was an agent with no facts, reaching for the only meeting
        it could see. Facts are cheap and they are already in `refs`.

        `when` is the EPOCH (the record's own shape) and `when_text` is the same moment rendered in
        THIS person's zone — the server can only render UTC, and a bare time in a zone nobody named
        is the kind of half-fact that reads as a bug. `state` is NOT set here: agent-api computes it
        at mint and RE-COMPUTES it at open, because a stranger who signs in between the mail and the
        click is not a stranger any more."""
        refs = {}
        for key in ("title", "organizer", "participants", "participant_names"):
            if ctx.refs.get(key):
                refs[key] = ctx.refs[key]
        start = ctx.refs.get("start")
        if start:
            refs["when"] = start
            try:
                refs["when_text"] = _their_clock(uid, start)
            except Exception:  # noqa: BLE001 — a clock we cannot render is not a reason to fail a mint
                pass
        # WHERE THIS MEETING'S RECORD WILL LIVE ON THE READER'S DESK — carried, not derived. See
        # `_note_path`. Computed even before `drop_to_attendees` has written it: the path is a
        # function of the meeting's day and title, both known at mint, and a tab naming a file
        # that does not exist yet is the documented, tested behaviour ("it appears when the
        # conversation (or a meeting) writes one"). What was NOT survivable was naming a file
        # nothing would ever write.
        if ctx.refs.get("title"):
            try:
                refs["note_path"] = _note_path(ctx, uid, ctx.refs["title"])
            except Exception:  # noqa: BLE001 — a path we cannot compute is not a reason to fail a mint
                pass
        return refs

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
        """RETIRED BY DECISION 29 (2026-09-02). Kept as a no-op, for the reason above.

        It emitted `onboarding.person.needed` / `onboarding.group.needed`, which started the email
        conversations that chased a person until they finished setup. Those flows are gone, so the
        facts now have no consumer — and a step that emits a fact nothing reacts to is worse than
        one that does nothing, because it leaves rows in the log implying work that never existed.

        The invite's touches are RSVP accept, the ack mail, and the prepare mail. Three, and no
        others. Group setup happens in a chat behind a `group-setup` scaffold, where the person is
        there to be asked. Result: {} — no reactions created, deliberately."""
        return Done({"retired": "decision 29 — the invite starts no email onboarding"})

    def _spawn_onboardings_retired(ctx: StepCtx):
        """The pre-decision-29 body, kept only so the diff shows what was removed rather than
        hiding it behind a rewrite. Unregistered and unreachable."""
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
        """RETIRED BY DECISION 29 (2026-09-02). Kept as a no-op, and deliberately not deleted.

        It was the queue gate: minutes waited for `.scaffolded`, and while they waited it mailed
        "Your minutes are waiting / Finish the setup conversation and the minutes arrive right
        after". The founder's ruling on reading that mail was "no we do not want that", and the
        step is out of `post_meeting` from version 4.

        IT STAYS REGISTERED, AND EMPTY, so that any reaction still in flight on an older version —
        or any DB-authored version that still names it — DRAINS instead of dying on "unknown step",
        and above all so that the nudge cannot be sent by a code path nobody remembered. Deleting
        the function would have left the mail reachable from a stale flow row; emptying it makes it
        unreachable from anywhere. Result: {ready} — the shape the old receipts carry."""
        return Done({"ready": True, "retired": "decision 29 — minutes are never gated on setup"})

    @reg.step
    def process_meeting(ctx: StepCtx):
        """ONE REAL AGENT TURN on session meet-<id>, producing ONE SHARED ARTEFACT: the meeting's
        report, the same words for everybody who was in the room.

        IT WRITES INTO NO DESK (founder decision 22, 2026-09-02). Not the organiser's either. The
        canonical home of the note is THE MEETING ROW AND ITS TRANSCRIPT STORE; every attendee's
        desk — the organiser's included, nobody special — receives the artefact itself afterwards,
        from `drop_to_attendees`. One meeting, one artefact, and the desks are where it lands, not
        where it lives.

        COMPLETION IS THE REPLY, GROUNDED IN THE TRANSCRIPT. It used to be a new commit touching
        `kg/entities/meeting/` in the organiser's repo (`ag.latest_meeting_note`, now deleted with
        its baseline). With no desk write that commit never happens, so that detector would wait
        fifteen minutes and fail every meeting — a silently never-completing step, which is worse
        than a loud one. The turn's own reply IS the artefact: the kick already required the reply
        to BE the report, because it was already being mailed verbatim.

        THE GROUNDING GATE IS UNCHANGED and now matters more, since the reply is no longer
        cross-checkable against a committed file: does the report share any six-word run with the
        actual transcript? If not the agent is told exactly that, once, and asked again; if it
        still cannot ground it the reaction FAILS LOUDLY rather than mailing a report nobody can
        trace to the meeting.

        THE ROOM. The turn may READ the desks of the people on the invite, prioritised by speaking
        time and capped by `room_read_max`. Flows proposes; agent-api verifies, resolves each
        address to a subject, and mounts only those who already have a desk — so nothing here
        mounts anything and no account is ever minted for the room.

        THE GROUP, and ONLY when there is one: the group desk is mounted read/write and this turn
        MAINTAINS it — its people, decisions, open items, README — rather than appending an
        artefact to it. A meeting with no group gets none of that.

        Reads: refs.{uid,meeting_id,native,participants?,participant_names?,group?}
        Effect: one agent turn · Result: {report, group, room_read}."""
        uid = ctx.refs["uid"]
        session = f"meet-{ctx.refs['meeting_id']}"
        group = str(ctx.refs.get("group") or "").strip()
        if "baseline" not in ctx.scratch:
            kick = prompt_for(ctx, "process-meeting.md", PROCESS_KICKOFF).format(
                mid=ctx.refs["meeting_id"], native=ctx.refs["native"],
                date=_meeting_stamp(ctx, uid))
            # WHOSE DESKS THIS TURN MAY READ — the invite, ordered by who spoke, capped. Computed
            # here because flows is where the transcript is reachable, and sent as a PROPOSAL:
            # agent-api verifies membership itself and mounts only people who already have a desk.
            # A matcher that cannot match degrades to invite order, never to an empty room.
            room_read = mt.room_order(uid, ctx.refs["meeting_id"],
                                      ctx.refs.get("participants") or [],
                                      ctx.refs.get("participant_names") or {},
                                      cap=_room_read_max(ctx))
            ctx.scratch["room_read"] = room_read
            # THE ROW ID, not refs["meeting_id"] — the room gate resolves a MEETINGS-DOMAIN ROW,
            # and refs may still carry a native id from meeting_ref(). This is the same identity
            # bug that mailed meeting 97's attendees a link with no token: `platform='unknown'`
            # with an empty native is addressed by NO pair, and only the row id always exists.
            row = mt.meeting_row(uid, ctx.refs.get("meeting_id"), ctx.refs.get("native"))
            row_id = (row or {}).get("id") if isinstance(row, dict) else None
            kick += _shared_report_rules(room_read, group)
            ctx.scratch["baseline"] = ag.dispatch_turn(
                uid, session, kick,
                room={"meeting_id": row_id, "read": room_read,
                      "names": ctx.refs.get("participant_names") or {},
                      "read_max": _room_read_max(ctx)} if row_id else None)
            # THE BEFORE WITNESS for the no-desk-write detector below. Taken here, once, rather
            # than at the check: the regrounding branch re-dispatches, and a witness re-read after
            # a stray commit would have already absorbed it.
            ctx.scratch["head_before"] = ag.head_sha(uid)
            return Wait(seconds=12)
        reply = ag.collect_reply(uid, session, ctx.scratch["baseline"])
        if reply is not None:
            # THE GROUNDING GATE. Removing the transcript from the event made the report depend on
            # the agent CHOOSING to fetch it, and measured on Haiku it chooses to about half the
            # time — and when it does not, it writes a confident report anyway, from the title and
            # the prompt. That is strictly worse than the truncated copy it replaced: a shallow
            # report is visibly shallow, a fabricated one is not. An instruction is not a gate.
            if not mt.grounded_in(reply, mt.transcript_text(uid, ctx.refs["meeting_id"])):
                if not ctx.scratch.get("regrounded"):
                    ctx.scratch["regrounded"] = True
                    ctx.scratch["baseline"] = ag.dispatch_turn(
                        uid, session,
                        "STOP. The report you just wrote contains nothing that appears in the "
                        f"meeting. You did not read it. Call mcp__vexa__meeting_transcript with "
                        f"meeting_id={ctx.refs['meeting_id']} and tail=0 NOW, read every segment, "
                        "then write it again from what it returns — quoting one verbatim "
                        "sentence with its speaker. If you cannot call that tool, say so.")
                    return Wait(seconds=12)
                raise StepError(
                    "the report is not grounded in the transcript — the agent did not read the "
                    "meeting, twice. Refusing to mail a report that cannot be traced to it.",
                    retryable=False)
            # THE NO-DESK-WRITE DETECTOR (decision 22). This step's contract is that it writes
            # into NO desk, and until now NOTHING CHECKED. On 2026-09-02 the turn committed a
            # 639-line raw transcript dump to the root of the organiser's desk, this step returned
            # Done, the minutes mail went out, and it surfaced only because somebody read the git
            # log by hand. The old completion test — a commit touching `kg/entities/meeting/` —
            # was deleted when the contract inverted, and its replacement watches the REPLY. So
            # the single most likely way for this turn to misbehave became the one unobserved
            # thing, which is the same defect shape as every other silent success here.
            #
            # ONLY the organiser's desk. Room desks are mounted read-only by agent-api; the GROUP
            # desk is the one place this turn is meant to write. Neither belongs in this check.
            #
            # LOUD, and NOT retryable: a retry re-runs the turn and the stray commit is still
            # there. Failing costs one meeting its mail and names the commit to remove. Passing
            # silently costs every meeting the desk it was supposed to land on — which is what
            # happened, four times over, before anyone noticed.
            before = ctx.scratch.get("head_before") or ""
            after = ag.head_sha(uid)
            if before and after and before != after:
                raise StepError(
                    "this turn committed to the organiser's desk, and it must not (decision 22): "
                    f"HEAD moved {before[:9]} -> {after[:9]}. Landed: "
                    f"{'; '.join(ag.head_subjects(uid)) or '(unreadable)'}. The report IS the "
                    "reply; desks are written by drop_to_attendees. Remove the stray commit, then "
                    "check the LIVE kick (`_global/prompts/process-meeting.md` if an admin wrote "
                    "one, else the baked `behavior/prompts/process-meeting.md`) for a "
                    "file-writing instruction that came back.",
                    retryable=False)
            return Done({"report": reply[:6000], "group": group,
                         "room_read": ctx.scratch.get("room_read", [])})
        # Still running: the long wait stays, because a turn that is genuinely working is allowed
        # to take its time. What is GONE is the "turn ended but wrote no note" branch — with the
        # reply itself as the artefact, a finished turn and a finished artefact are the same
        # event, and there is no longer a state where one exists without the other.
        if ctx.clock_now - ctx.scratch.get("t0", ctx.scratch.setdefault("t0", ctx.clock_now)) > 900:
            raise StepError("the agent turn never finished (no reply after 15min)", retryable=False)
        return Wait(seconds=10)

    def _shared_report_rules(room_read: list, group: str) -> str:
        """What the post-meeting turn is told on top of the behavior-domain kick.

    ⚠ IT ONCE CONTRADICTED THAT KICK, and the contradiction shipped. `behavior/prompts/
        process-meeting.md` still said "write the meeting note at kg/entities/meeting/... update
        the index... update README.md" — the desk writes decision 22 removed — while this block
        said the opposite, and the model was left to resolve it. It resolved it by writing a raw
        transcript dump to the organiser's desk root. That file has since been corrected (F54,
        2026-09-02) and `process_meeting` now VERIFIES the desk did not move rather than trusting
        either text.

        The WRITE NO FILES clause below stays anyway, and deliberately: `prompt_for` reads a live
        `_global/prompts/<name>` override before the baked file, so an admin can put the old
        instruction back without touching this repo. Belt and braces, with the detector as the
        actual guarantee. (Until R-B18 that override was asserted here and performed nowhere — the
        sentence was true of the design and false of the code for as long as both existed.)
        """
        block = (
            "\n\nTHE REPORT IS SHARED, AND IT IS YOUR REPLY. One report for this meeting, the "
            "same words to everybody who was in the room — the organiser and every attendee read "
            "the identical mail. Do not write a section per person, do not address anyone "
            "individually, and do not write anything only one reader is meant to see.\n\n"
            "WRITE NO FILES FOR THIS REPORT. Ignore any instruction above to save the note into a "
            "workspace, to update an index, or to update a README: this report is not filed "
            "anywhere by you. Its home is the meeting itself, and every person in the room gets a "
            "copy on their own desk afterwards, which is not your job either. Your REPLY is the "
            "artefact — it is mailed verbatim, so no preamble and no meta-commentary.\n\n"
            "MEETING-RELEVANT FACTS ONLY, ATTRIBUTED — a person's desk informs the report, it is "
            "never quoted into it. ")
        if room_read:
            block += (
                "You have READ-ONLY access to the desks of the people who were in this meeting ("
                + ", ".join(room_read) + "). Use them to understand what was said and to attribute "
                "it correctly, and never copy a line, a note or a phrase out of one into this "
                "report — a report that goes to everyone in the room is not a place where one "
                "person's own notes can appear. ")
        block += ("Everything in the report was said, decided, committed or asked IN THIS ROOM.\n\n"
                  "Anything person-centric happens when they click the link in the mail, not here.")
        if group:
            block += (
                f"\n\nTHIS MEETING BELONGS TO THE GROUP #{group}, AND ITS DESK IS YOURS TO "
                "MAINTAIN. You have it mounted READ/WRITE — the one desk you write to in this "
                "turn. Maintaining is not appending: bring the group's own pages up to date with "
                "what this meeting changed.\n"
                "  - its PEOPLE: who is in this group, what each of them is carrying now\n"
                "  - its DECISIONS: add what was decided, and correct anything this meeting "
                "overturned rather than leaving both\n"
                "  - its OPEN ITEMS: close what closed, add what opened, re-own what moved\n"
                "  - its README: the dashboard a member reads first — make it true as of today\n"
                "Edit the pages that exist before creating new ones, and never copy one person's "
                "desk into the group's.")
        return block

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
        # THE ONE ARTEFACT, off the receipt. It used to be re-read out of the organiser's desk
        # (`ws_file(uid, note_path)`), which no longer holds it — the run writes into no desk, so
        # `process_meeting`'s reply IS the report and the receipt is where it lives. The commit sha
        # went with the desk write it referred to.
        report = _readable(ctx.prior["process_meeting"]["report"])
        body = (_provenance(ctx, ctx.refs["uid"], to_attendee=False)
                + report + "\n\n—\nRecorded by Vexa\n"
                "Reply to this email with corrections or questions — I'll update what we hold "
                "and answer here. Or open it and talk it through:")
        # THE SCAFFOLD, not a raw deeplink (PRD §5.5). No share token: this is the organiser's own
        # meeting, and a capability nobody needs is a capability nobody should be handed.
        link = mint_scaffold(
            "post-meeting", ctx.refs["organizer"], opening="minutes-review",
            meeting_id=ctx.refs["meeting_id"], refs=_scaffold_refs(ctx, ctx.refs["uid"]),
            provenance={"flow": "post_meeting", "step": "email_minutes",
                        "reaction_id": str(getattr(ctx, "reaction_id", "") or ""),
                        "minted_by": str(ctx.refs["uid"])})
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
        """`room_read_max` — DEFAULT 12. How many DESKS the post-meeting turn may have read-only
        mounts for.

        Founder, 2026-09-02: *"need to make sure agent will not die if it has 200 folders in it."*
        The cap is on MOUNTS, not on people: everybody on the invite still gets the mail and the
        artefact on their desk, because those are a write per person and cost nothing per head.
        Reading is what does not scale.

        It caps a room whose MEMBERSHIP is the invite — speaking time only decides who is at the
        front of the list, so the cap is what turns "everyone in the room" into "the twelve most
        likely to explain it". A non-numeric or non-positive value is the default, never an error:
        zero would be indistinguishable from "unset" while meaning the opposite, so it is not a way
        to say "mount nobody"; agent-api's own verification owns that."""
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
        # THE ONE ARTEFACT. `_readable` turns the agent's report into something a person meets in
        # a mail (frontmatter off, wikilinks flattened, relative links absolutised); it is the same
        # string `email_minutes` puts in front of the organiser and the same one every attendee's
        # desk receives, which is what "the same report" means operationally rather than as an
        # intention.
        report = _readable(ctx.prior["process_meeting"]["report"]).strip()
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
        # THIS RUN's failures, keyed by address so a retry replaces the previous verdict instead
        # of appending a second copy of it. The old list appended blindly, so an address that
        # failed three times appeared three times in the receipt.
        failures: dict[str, str] = {}
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
            #
            # THE SCAFFOLD (PRD §5.5). It carries the share token the line above minted, so the one
            # button in this mail opens a chat that can actually see the meeting — and the mint
            # RAISES rather than returning a weaker link, which puts a failure here into the same
            # HELD branch as a failed share. Two ways to send a dead button, one refusal for both.
            kind = "invite-offer" if ask.endswith("-invite") else "post-meeting"
            try:
                link = mint_scaffold(
                    kind, a, opening=ask, meeting_id=mid, share_token=token,
                    refs=_scaffold_refs(ctx, ctx.refs["uid"]),
                    provenance={"flow": "post_meeting", "step": "email_attendees",
                                "reaction_id": str(getattr(ctx, "reaction_id", "") or ""),
                                "minted_by": str(ctx.refs["uid"])})
            except StepError as e:
                pending = [x for x in who if x not in sent]
                raise StepError(
                    f"HELD the attendee fan-out for meeting {mid}: no scaffold could be minted for "
                    f"{a} ({e}). Mailed: {', '.join(sent) or 'nobody'}. "
                    f"NOT mailed: {', '.join(pending)}.",
                    retryable=getattr(e, "retryable", False)) from e
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
                failures[a] = f"{a}: {type(e).__name__}: {e}"[:240]
            # ONE HEARTBEAT PER PERSON. Everything above costs a share mint, a scaffold mint and
            # an SMTP round trip, so a full room runs past the 90 s lease; without this the
            # reclaimer hands the reaction to a second worker that starts from an empty `sent`
            # and mails the whole room again, with a second share token each. `checkpoint` both
            # renews the lease and persists what has been done — see `flows/loop.py`.
            ctx.checkpoint()
        failed = [failures[a] for a in sorted(failures)]
        ctx.scratch["failed"] = failed
        # A FAILED SEND IS RETRIED, not forgotten. It used to be neither: the address entered
        # neither `sent` nor `drops`, the step returned `Done`, and one transient SMTP 421
        # removed a person from a meeting they had attended — permanently and silently, because
        # `Done` is what "the report went out" looks like from every consumer.
        #
        # ...and it is retried a BOUNDED number of times, then given up on OUT LOUD. Raising
        # until the engine's own ceiling would fail the whole reaction, and `drop_to_attendees`
        # runs after this step: one dead address in a twenty-person invite would then cost the
        # other nineteen the record on their desk. The ceiling keeps both promises.
        attempt = int(getattr(ctx.reaction, "attempt", 1) or 1)
        if failed and attempt < ATTENDEE_MAIL_ATTEMPTS:
            raise StepError(
                f"could not mail {len(failed)} of {len(who)} attendee(s) for meeting {mid} "
                f"(attempt {attempt} of {ATTENDEE_MAIL_ATTEMPTS}): " + " · ".join(failed)
                + f". Mailed: {', '.join(sent) or 'nobody'}.", retryable=True)
        return Done({"sent": len(sent), "followup": "on", "to": sent, "meeting_id": mid,
                     "drops": drops,               # what drop_to_attendees writes, per person
                     "failed": failed})

    # ── the drop — one meeting entity into each attendee's own workspace (PRD decision 20) ──
    def _yaml(value: str) -> str:
        """One frontmatter scalar, always double-quoted. A meeting title legitimately contains
        `:`, `#`, `[`, quotes and emoji; unquoted, any of them turns the entity's own frontmatter
        into something a parser reads differently from what we wrote."""
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

    def _drop_entity(*, title, day, entity_id, date_prose, organizer, participants, report,
                     link) -> str:
        """THE ARTEFACT ITSELF, in the shape `kg/templates/meeting.md` defines — not a pointer to
        somebody else's copy of it (founder decision 22, 2026-09-02).

        There is no longer a copy elsewhere to point AT: the run writes into no desk, the note's
        canonical home is the meeting row and its transcript store, and every person who was in
        the room gets the report on their own desk. The organiser included, nobody special.

        THE SAME BYTES FOR EVERYONE except the last line. Frontmatter, heading, provenance line and
        report are identical in every desk this is written to — the entity is a fact about the
        meeting, and a fact that differs per reader is not one. The link differs because it carries
        that person's own restricted share token, and a forwarded link must grant its new reader
        nothing.

        `participants` is the meeting's own roster, so the frontmatter says who was in the room
        rather than who this copy happens to belong to — which is also what keeps the bytes equal.

        `entity_id` IS THE FILENAME'S STEM, passed in rather than rebuilt. It used to be composed
        here as `f"{day}-{_slug(title)}"` — a fourth independent spelling of one identity, beside
        the writer's path, the kick's, and the terminal's. It agreed with the filename only by
        coincidence, and stopped agreeing the moment the filename gained the meeting's time
        (F58). An id that does not match the file it is in is worse than no id: every consumer
        that resolves one from the other silently misses."""
        roster = ", ".join(_yaml(a) for a in participants)
        lines = [
            "---",
            "type: meeting",
            f"id: {entity_id}",
            f"title: {_yaml(title)}",
            f"date: {day}",
            f"organizer: {_yaml(organizer)}",
            f"participants: [{roster}]",
            "tags: [vexa-meeting]",
            "---",
            "",
            f"# {title}",
            "",
            f"{date_prose} — {organizer} had Vexa in the room.",
            "",
            (report or "").strip(),
            "",
        ]
        # NO LINK, NO LINE. Since the drop room is the invite rather than the mailing list, a
        # person who was not mailed has no share capability of their own — and a trailer reading
        # "Open the meeting: " with nothing after it is a broken affordance, while somebody
        # else's link would be the one thing a share token exists to prevent. They get the
        # artefact, in full, and no button.
        if str(link or "").strip():
            lines += ["---", "", f"Open the meeting: {link}", ""]
        return "\n".join(lines)

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
        """The meeting's ARTEFACT into every desk in the room — the organiser's included. Plain
        code, no agent turn, no LLM (founder decisions 20 and 22).

        This is where the meeting lands on a person's desk. `process_meeting` writes into no desk
        at all, so nothing else does it: one meeting produces one artefact, and this step copies
        that same artefact, byte-for-byte, to everybody who was in the room. The only thing that
        differs between two people's copies is the `?meeting=` link, which carries their own share
        token because a forwarded link must grant its new reader nothing.

        WHO. The organiser, plus every attendee `email_attendees` ACTUALLY mailed (its `drops`
        payload carries the exact link each of them was given, so nothing is recomputed and no
        second share capability is minted). The organiser is not special: they get the same entity,
        with the link `email_minutes` already built for them.

        PER PERSON, three effects and no others:
          1. their platform user (`ensure_platform_user`) and their desk
             (`POST /api/workspace/init` AS THEM — the same seeding the click does). Nothing else
             is built: no chat, no session, no scaffolding.
          2. `kg/entities/meeting/<date>-<slug>.md` — the report, with the meeting's title, date,
             organiser and roster as frontmatter, and their own link at the foot.
          3. `kg/entities/meeting/index.md` gains one line, once.
        Both writes go through `PUT /api/workspace/file`, which commits, so each lands in that
        desk's history rather than as an untracked file.

        IT IS ENTITY-FREE, AND THAT IS AN ECONOMIC BOUND, NOT AN OVERSIGHT (founder, decision 22
        addendum). No person entity, no company entity, no decision entity, no README rewrite, and
        no agent turn — the whole step is plain code. *A desk nobody talks to is a flat pile of
        reports: complete, and free.* Wiring that pile into entities costs a model call per person
        per meeting, and for somebody who never opens the product it buys nothing. The wiring
        happens when the person ENGAGES, in their own chat, proposed by the agent rather than run
        on their behalf. The one exception is the GROUP desk, which `process_meeting` maintains
        with entities because it is the room's shared state rather than one person's pile — and
        that is the group's desk, never an individual's.

        IT READS NO DESK. The only paths it reads anywhere are the two it is itself the author of —
        the entity above and that index — and it reads them for exactly one reason: so a second run
        writes nothing instead of a second entity, a second index line and a second commit. What it
        reads is compared against what it was about to write and then dropped: never returned,
        never logged, never shown to another person, never mixed into anyone else's file. Nothing
        belonging to one person reaches another.

        IDEMPOTENT per (meeting, person) twice over, because the two halves fail differently:
        `ctx.scratch` skips people already done inside this run (a `StepError` re-runs the whole
        step), and each write is a content-compare on a stable path, which is what survives a
        worker restart that loses scratch entirely.

        FAILURE POLICY: one person's drop failing must never cost the others theirs, so each is
        attempted in its own try and the failures are collected into the result — a partial drop is
        a fact an operator can see and re-run. The step fails only when EVERY drop failed, which is
        not one person's bad state but the agent-api being unreachable; retryable, since every
        write above is safe to repeat.

        Prior: process_meeting{report}, email_attendees{drops}, email_minutes{link}
        Effect: N desk writes · Result: {dropped, to, failed, entity}."""
        pm = ctx.prior.get("process_meeting") or {}
        report = _readable(pm.get("report") or "").strip()
        if not report:
            return Done({"dropped": 0, "to": [], "failed": [],
                         "skipped": "there is no report to drop"})
        uid = ctx.refs["uid"]
        title = ctx.refs.get("title") or "your meeting"
        organizer = ctx.refs.get("organizer") or "the organiser"
        day = _meeting_stamp(ctx, uid)[:10]          # the MEETING's day, in the organiser's zone
        date_prose = _meeting_date(ctx, uid)
        entity_path = _note_path(ctx, uid, title)      # the one recipe — see `_note_path`
        filename = entity_path.rsplit("/", 1)[-1]
        index_path = "kg/entities/meeting/index.md"
        att = ctx.prior.get("email_attendees") or {}
        roster = [str(a).strip().lower() for a in (ctx.refs.get("participants") or [])
                  if str(a).strip()]
        if organizer.lower() not in roster:
            roster = [organizer.lower()] + roster
        # THE ORGANISER IS ONE OF THE ROOM. Their link is the one `email_minutes` already built —
        # no share token, because the meeting is theirs — and when that step was skipped (their
        # `mail_minutes` is off) the same link is composed here rather than dropped: a preference
        # about MAIL is not a preference about what lands on their own desk.
        mid = att.get("meeting_id") or ctx.refs.get("meeting_id")
        organiser_link = (ctx.prior.get("email_minutes") or {}).get("link") \
            or mint_scaffold("post-meeting", organizer, opening="minutes-review", meeting_id=mid,
                             refs=_scaffold_refs(ctx, uid),
                             provenance={"flow": "post_meeting", "step": "drop_to_attendees",
                                         "reaction_id": str(getattr(ctx, "reaction_id", "") or ""),
                                         "minted_by": str(uid)})
        # THE ROOM IS THE INVITE, NOT THE MAILING LIST. This used to be
        # `[organiser] + att["drops"]`, and `drops` is empty whenever the attendee MAIL was
        # switched off (`attendee_followup`) or every attendee is outside the organiser's domain
        # (PRD §16.2's allow-list, which governs mail and nothing else). A preference about mail
        # was therefore silently a preference about whose desk the meeting reached — while
        # `room_order` had already MOUNTED those same desks to write the report. Decision 20 says
        # the drop goes into every attendee's workspace, creating it if absent; decision 22a says
        # the organiser's always does. `drops` now supplies one thing only: that person's own
        # share link, where they were mailed one.
        links = {str((d or {}).get("to") or "").strip().lower(): str((d or {}).get("link") or "")
                 for d in (att.get("drops") or [])}
        room = [{"to": organizer, "link": organiser_link}]
        room += [{"to": a, "link": links.get(a, "")}
                 for a in roster if a != organizer.lower()]
        entity_id = filename[:-3] if filename.endswith(".md") else filename
        body = _drop_entity(title=title, day=day, entity_id=entity_id, date_prose=date_prose,
                            organizer=organizer, participants=roster, report=report, link="")
        done = list(ctx.scratch.setdefault("dropped", []))
        failed = list(ctx.scratch.setdefault("drop_failed", []))
        for d in room:
            a = str((d or {}).get("to") or "").strip()
            if not a or a in done:
                continue
            try:
                their_uid = ensure_platform_user(a)
                ag.workspace_init(their_uid)
                _write_if_changed(their_uid, entity_path, _drop_entity(
                    title=title, day=day, entity_id=entity_id, date_prose=date_prose,
                    organizer=organizer, participants=roster, report=report,
                    link=d.get("link") or ""))
                _write_if_changed(their_uid, index_path, _index_entry(
                    ws_file(their_uid, index_path), title, filename, day))
                done.append(a)
                failed = [f for f in failed if not f.startswith(a + ":")]
            except Exception as e:  # noqa: BLE001 — one person never costs the rest theirs
                failed = [f for f in failed if not f.startswith(a + ":")]
                failed.append(f"{a}: {type(e).__name__}: {e}"[:240])
            ctx.scratch["dropped"] = done
            ctx.scratch["drop_failed"] = failed
            ctx.checkpoint()      # same shape as the fan-out above: N round trips, one lease
        if done:
            return Done({"dropped": len(done), "to": done, "failed": failed,
                         "entity": entity_path, "meeting_id": mid,
                         # every copy is these bytes plus that person's own link
                         "bytes": len(body)})
        raise StepError(
            f"every desk drop failed for meeting {mid} ({len(room)} person(s) in the room): "
            + " · ".join(failed), retryable=True)

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
        # THE SCAFFOLD (PRD §5.5). The row was planned above precisely so this link can name it;
        # the mint is the last check that the chat behind the button will hold the meeting, and it
        # RAISES rather than mailing a prepare note whose button opens a chat that knows nothing.
        link = mint_scaffold("prep", to, opening="prep", meeting_id=ref,
                             refs=_scaffold_refs(ctx, uid),
                             provenance={"flow": "meeting_prep", "step": "prepare_meeting",
                                         "reaction_id": str(getattr(ctx, "reaction_id", "") or ""),
                                         "minted_by": str(uid)})
        mid = notify(to, subject or f"Prepare: {title}", body, link=link)
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
    # VERSION 2 — `spawn_onboardings` removed (decision 29). The version bump is the whole
    # mechanism: `match()` is newest-wins, so a step list is changed by ADDING a version, never by
    # editing one in place, and a reaction already in flight keeps the version it was admitted on.
    reg.flow(name="invite_intake", version=2, on=INVITE,
             steps=[s["ensure_user"], s["rsvp_accept"], s["ack_by_email"],
                    s["emit_prep"],
                    s["await_start"], s["dispatch_bot"], s["run_meeting"], s["emit_completed"]])
    # `onboard_person` and `onboard_group` are RETIRED (decision 29) — the email conversations that
    # chased a person until they finished setup. They are not declared, so nothing reacts to
    # `onboarding.*.needed` any more, and `spawn_onboardings` no longer emits it either. Their
    # steps stay in the vocabulary; a flow is retired by not registering it, and `flows_submit`
    # would refuse to resurrect one anyway without a human writing the row.
    reg.flow(name="meeting_prep", version=1, on=UPCOMING,
             steps=[s["prepare_meeting"]])
    # VERSION 4 — `require_workspace` removed (decision 29). Four, not two, because versions 2 and
    # 3 were authored through the API against this same flow name and `match()` takes the newest
    # number wherever it came from; a code change that does not clear the highest DB version is
    # inert, which is exactly the defect `Registry.shadowing_versions` now warns about.
    reg.flow(name="post_meeting", version=4, on=COMPLETED,
             steps=[s["process_meeting"], s["email_minutes"],
                    s["email_attendees"], s["drop_to_attendees"]])
    reg.flow(name="email_chat", version=1, on=MAIL_REPLY,
             steps=[s["feedback_turn"], s["email_reply"]])
