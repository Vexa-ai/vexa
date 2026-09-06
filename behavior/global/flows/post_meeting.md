---
kind: flow
flow: post_meeting
version: 4
trigger: meeting.completed
steps: 4
generated: from the code that runs it — edits here are overwritten
---

# post_meeting

Runs when **`meeting.completed`** happens, in 4 steps. This page is written from the code — the docstrings below are the ones in the image that is running, and the Python at the foot is that code verbatim.

| | |
|---|---|
| **trigger** | `meeting.completed` |
| **version** | 4 — a step list changes by adding a version, never by editing one in place |
| **mails** | `(composed in the step, from no template)`, `attendee-head` |
| **rules it honours** | [`report_to_participants`](../POLICIES.md#report_to_participants), [`external_participants`](../POLICIES.md#external_participants), [`attendee_domains`](../POLICIES.md#attendee_domains), [`data_statement`](../POLICIES.md#data_statement) |

## The steps, in order

### 1. `process_meeting`

ONE REAL AGENT TURN on session meet-<id>, producing ONE SHARED ARTEFACT: the meeting's report, the same words for everybody who was in the room.

- **reads:** refs.{uid,meeting_id,native,participants?,participant_names?,group?}
- **effect:** one agent turn
- **result:** {report, group, room_read}
- **domains:** without **agent** this step is skipped and the flow carries on · without **meetings** the reaction ends there, saying so

### 2. `email_minutes`

Send the committed note VERBATIM in the body + the feedback ask + ONE link into the minutes terminal, already primed on this meeting. Cannot run before the commit: its input IS process_meeting's receipt.

- **reads:** refs.{uid,organizer,title,meeting_id}
- **effect:** one notification
- **domains:** without **agent** it runs anyway, with less to work with · without **meetings** the reaction ends there, saying so
- **mails:** `(composed in the step, from no template)`
- **rules it honours:** [`report_to_participants`](../POLICIES.md#report_to_participants), [`data_statement`](../POLICIES.md#data_statement)

### 3. `email_attendees`

Every inside-domain ATTENDEE gets the follow-up plus ONE button into a chat the click composes. Cannot run before the note: its input is process_meeting's receipt.

- **reads:** refs.{participants, organizer, title, meeting_id, share?}
- **effect:** N notifications
- **result:** {sent, followup, skipped, drops, failed}
- **domains:** without **agent** it runs anyway, with less to work with · without **meetings** the reaction ends there, saying so
- **mails:** `attendee-head`
- **rules it honours:** [`report_to_participants`](../POLICIES.md#report_to_participants), [`external_participants`](../POLICIES.md#external_participants), [`attendee_domains`](../POLICIES.md#attendee_domains)

### 4. `drop_to_attendees`

The meeting's ARTEFACT into every desk in the room — the organiser's included. Plain code, no agent turn, no LLM (founder decisions 20 and 22).

- **effect:** N desk writes
- **result:** {dropped, to, failed, entity, proposed}
- **domains:** without **agent** this step is skipped and the flow carries on · without **meetings** the reaction ends there, saying so

## The code

Read-only, and the same bytes the image runs. It is here because the founder asked whether we can show it: the page is the explanation, this is the appendix.

<ViewSource step="process_meeting">

```python
@reg.step(needs=("agent", "meetings"), absent={"agent": "skip"})
def process_meeting(ctx: StepCtx):
    """ONE REAL AGENT TURN on session meet-<id>, producing ONE SHARED ARTEFACT: the meeting's
    report, the same words for everybody who was in the room.

    IT WRITES INTO NO DESK (founder decision 22, 2026-09-02). Not the organiser's either. The
    canonical home of the note is THE MEETING ROW AND ITS TRANSCRIPT STORE; every attendee's
    desk — the organiser's included, nobody special — receives the artefact itself afterwards,
    from `drop_to_attendees`. One meeting, one artefact, and the desks are where it lands, not
    where it lives.

    AND THAT IS ENFORCED BY THE MOUNTS (Vexa-ai/vexa#1606, 2026-09-06). agent-api mounts every
    desk this subject owns READ-ONLY for a room run, so the turn cannot write one however it is
    instructed. The HEAD-before/HEAD-after check below stays as the last line — it measures the
    repository rather than trusting the mount table — but it is no longer the thing standing
    between decision 22 and a desk write, which is why it could fail a meeting's minutes twice
    in one day while being entirely correct.

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
        # NO KICK, NO TURN. An unmounted behavior tree is a deployment fact (decision 43.12),
        # so it lands as the same terminal `not_present` an absent agent domain does — never as
        # an exception, and never as a turn dispatched with an empty instruction, which would
        # bill a model to produce a report nobody could ground.
        try:
            kick = prompt_for(ctx, "process-meeting.md").format(
                mid=ctx.refs["meeting_id"], native=ctx.refs["native"],
                date=_meeting_stamp(ctx, uid))
        except PromptAbsent as absent:
            return NotPresent("behavior", detail=str(absent))
        # WHOSE DESKS THIS TURN MAY READ — the invite, ordered by who spoke, capped. Computed
        # here because flows is where the transcript is reachable, and sent as a PROPOSAL:
        # agent-api verifies membership itself and mounts only people who already have a desk.
        # A matcher that cannot match degrades to invite order, never to an empty room.
        # THE FULL ORDERED LIST, uncut (R-B17). The cap used to be applied HERE, to
        # ADDRESSES, and again in agent-api, to MOUNTED DESKS — and agent-api's own comment
        # says why the second one is the right one: "capping before resolution would silently
        # under-fill the room". Twelve addresses of which nine have no desk is a three-desk
        # room. Flows orders; agent-api resolves, then cuts at `read_max`.
        read_max = _room_read_max(ctx)
        room_read = mt.room_order(uid, ctx.refs["meeting_id"],
                                  ctx.refs.get("participants") or [],
                                  ctx.refs.get("participant_names") or {})
        ctx.scratch["room_read"] = room_read
        # THE ROW ID, not refs["meeting_id"] — the room gate resolves a MEETINGS-DOMAIN ROW,
        # and refs may still carry a native id from meeting_ref(). This is the same identity
        # bug that mailed meeting 97's attendees a link with no token: `platform='unknown'`
        # with an empty native is addressed by NO pair, and only the row id always exists.
        row = mt.meeting_row(uid, ctx.refs.get("meeting_id"), ctx.refs.get("native"))
        row_id = (row or {}).get("id") if isinstance(row, dict) else None
        # THE ROW ID, STASHED. The grounding gate below needs the same identity this dispatch
        # used, and it was reading `refs["meeting_id"]` instead — the ref, which may still be a
        # native id (R-B19).
        ctx.scratch["row_id"] = row_id
        # The PROMPT names only as many desks as agent-api will actually mount: the wire
        # carries the whole ordered room, the sentence must not claim more than the cap allows.
        kick += _shared_report_rules(room_read[:read_max] if read_max else room_read, group)
        ctx.scratch["baseline"] = ag.dispatch_turn(
            uid, session, kick,
            room={"meeting_id": row_id, "read": room_read,
                  "names": ctx.refs.get("participant_names") or {},
                  "read_max": read_max} if row_id else None,
            # NOBODY TYPED THIS KICK (Vexa-ai/vexa#1605) — agent-api marks the turn with these
            # two and the chat shows "Meeting processed" where the instruction block was.
            flow=ctx.reaction.flow, step=ctx.reaction.step)
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
        # THE RESOLVED ROW, not the ref (R-B19). The dispatch two screens up already resolved
        # one and said why: `platform='unknown'` with an empty native is addressed by no pair,
        # and only the row id always exists. This line kept using the ref, so on exactly those
        # meetings the read 404'd — and an unreadable transcript used to answer `""`, which
        # `grounded_in` treats as "no speech captured" and passes. The gate switched itself off
        # precisely when the identity was broken, which is when it was the only thing left.
        transcript = mt.transcript_text(uid, ctx.scratch.get("row_id")
                                        or ctx.refs["meeting_id"])
        if transcript is None:
            raise StepError(
                "the meeting's transcript could not be read, so the report cannot be checked "
                f"against it (meeting row {ctx.scratch.get('row_id') or '?'}, ref "
                f"{ctx.refs['meeting_id']}). Refusing to mail an unverifiable report — this is "
                "a broken read, not a quiet meeting, and the two must not look the same.",
                retryable=True)
        if not mt.grounded_in(reply, transcript):
            if not ctx.scratch.get("regrounded"):
                ctx.scratch["regrounded"] = True
                ctx.scratch["baseline"] = ag.dispatch_turn(
                    uid, session,
                    "STOP. The report you just wrote contains nothing that appears in the "
                    f"meeting. You did not read it. Call mcp__vexa__meeting_transcript with "
                    f"meeting_id={ctx.refs['meeting_id']} and tail=0 NOW, read every segment, "
                    "then write it again from what it returns — quoting one verbatim "
                    "sentence with its speaker. If you cannot call that tool, say so.",
                    flow=ctx.reaction.flow, step=ctx.reaction.step)
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
        # ⚠ IT IS THE LAST LINE NOW, NOT THE ONLY ONE (Vexa-ai/vexa#1606). Decision 22 is
        # enforced by the MOUNTS: `build_mount_set` gives a room run no writable desk of the
        # subject's own, so on a group-less meeting there is nothing for an end-of-turn writer
        # to commit to. This check stays because a mount table is a claim about a deployment and
        # this is a measurement of the actual repository — but it should never fire again.
        #
        # AND WHEN IT DOES, IT REPAIRS ITSELF ONCE. It used to be flatly `retryable=False`, with
        # the true reason that "a retry re-runs the turn and the stray commit is still there" —
        # which made the recovery a HUMAN: reset the desk to the sha in this message, then
        # `POST /reactions/<id>/retry`. That happened twice on 2026-09-06 and both meetings lost
        # their minutes in the meantime. So the step now performs exactly those two acts itself,
        # once: `ag.reset_desk` puts HEAD back on the witness (backward-only, the caller's own
        # desk, internal tier), and the turn is re-dispatched with the stray commits named to it.
        # A second failure, or a reset that refuses, IS terminal — and it says the commits and
        # the one command, so the human recovery is a copy-paste rather than a reconstruction.
        before = ctx.scratch.get("head_before") or ""
        after = ag.head_sha(uid)
        if before and after and before != after:
            landed = "; ".join(ag.head_subjects(uid)) or "(unreadable)"
            if not ctx.scratch.get("desk_reset"):
                ctx.scratch["desk_reset"] = True
                undo = ag.reset_desk(uid, before, reason=f"decision 22 · meeting {ctx.refs['meeting_id']}")
                if undo.get("reset"):
                    ctx.scratch["baseline"] = ag.dispatch_turn(
                        uid, session,
                        "STOP. That turn WROTE TO A DESK, and this run writes to none. It "
                        f"committed: {landed}. Those commits have been removed. Write the "
                        "report again as your REPLY and nothing else — create no file, update "
                        "no index, update no README. The reply is mailed verbatim, so no "
                        "preamble and no meta-commentary.",
                        flow=ctx.reaction.flow, step=ctx.reaction.step)
                    return Wait(seconds=12)
                landed += f" · reset refused: {undo.get('detail') or 'no detail'}"
            raise StepError(
                "this turn committed to the organiser's desk, and it must not (decision 22): "
                f"HEAD moved {before[:9]} -> {after[:9]}. Landed: {landed}. The report IS the "
                "reply; desks are written by drop_to_attendees. This step already tried to undo "
                "it once and re-ran the turn. To recover by hand, put the desk back:\n"
                f"  curl -fsS -X POST \"$AGENT_API_URL/api/workspace/git/reset\" "
                f"-H 'X-User-Id: {uid}' -H \"X-Internal-Secret: $INTERNAL_API_SECRET\" "
                f"-H 'Content-Type: application/json' -d '{{\"sha\":\"{before}\"}}'\n"
                "then retry the reaction. If it keeps happening, the writer is in the LIVE kick "
                "(`_global/prompts/process-meeting.md` if an admin wrote one, else the baked "
                "`behavior/prompts/process-meeting.md`) or a mount that should not be writable "
                "on a room run (`control_plane.dispatch.build_mount_set`).",
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
```

</ViewSource>

<ViewSource step="email_minutes">

```python
@reg.step(needs=("agent", "meetings"), absent={"agent": "degrade"})
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

    WITH NO AGENT DOMAIN THIS STEP STILL SENDS, in a different shape (F-D20). `process_meeting`
    is skipped there, so there is no report and no chat to link into — but the meeting WAS
    recorded, by the meetings domain, which is deployed (this step still aborts when it is
    not). The degraded mail says exactly that and no more: it never says "Minutes", because
    there are none, and it never carries a link, because there is nothing behind one. Claiming
    a report that was never written is the failure `NotPresent` itself exists to prevent, one
    layer out.

    Reads: refs.{uid,organizer,title,meeting_id} · Effect: one notification."""
    if not setting(ctx.refs["uid"], "mail_minutes"):
        return Done({"skipped": "mail_minutes is off for this person"})
    # THE RECIPIENT. `refs["organizer"]` only exists for a calendar-invited meeting — an AD
    # HOC one (F212, 2026-09-03) carries no organizer at all, and `uid` IS the person: they
    # dispatched the bot with their own gateway key, so their address is one lookup away
    # (`_organizer_address`). Neither resolving is not retryable — the refs are frozen at
    # admission, so a retry would ask the same unanswerable question again — and it names the
    # uid so an operator reading the reason knows exactly which account has no address.
    organizer = _organizer_address(ctx)
    if not organizer:
        raise StepError(
            f"cannot mail meeting {ctx.refs.get('meeting_id')!r}: no organizer on the ref and "
            f"no address for uid {ctx.refs.get('uid')!r} — refusing to mail nobody.",
            retryable=False)
    # THE ONE ARTEFACT, off the receipt. It used to be re-read out of the organiser's desk
    # (`ws_file(uid, note_path)`), which no longer holds it — the run writes into no desk, so
    # `process_meeting`'s reply IS the report and the receipt is where it lives. The commit sha
    # went with the desk write it referred to.
    # THE REPORT, OR ITS HONEST ABSENCE. A skipped `process_meeting` leaves a CONFIRMED
    # receipt carrying `{"outcome": "skipped", …}` rather than no receipt at all, so `.get`
    # here answers "there is no report" for the one case that produces none, and would still
    # raise for a genuinely missing prior — which is a different bug and must stay loud.
    written = (ctx.prior.get("process_meeting") or {}).get("report")
    if written is None:
        return _mail_the_recording(ctx)
    report = _readable(written)
    # WHERE THE CREATOR LEARNS THEY CAN SAY NO. Default-ON sharing with an opt-out nobody can
    # find is default-ON sharing, and under a works council the defensible part of the default
    # is precisely that the creator decides (PRD §16.2 item 3). This is the only mail the
    # creator reliably gets, so it is the only place the token can live and be seen.
    #
    # CONDITIONAL, because the sentence is a claim about what is about to happen. On a
    # deployment with `attendee_followup: off`, or on a meeting already opted out, nobody else
    # is getting these notes and saying so would be false — the same rule that stops the
    # degraded mail from using the word "Minutes".
    #
    # This one sentence is hard-coded where the rest of the wording is a file. Written down
    # rather than discovered later (`behavior/mail/README.md` says so too): `email_minutes`
    # composes its body inline and renders no template, so there is no file for it to live in
    # yet. It moves the moment this step renders `minutes-head.md`.
    sharing = ("\nEveryone else inside your organisation who was on the invite gets these "
               "notes too. To keep one meeting to yourself, put #noshare in the invite.\n"
               if _followup_on(ctx) else "")
    body = (_provenance(ctx, ctx.refs["uid"], to_attendee=False)
            + report + "\n\n—\nRecorded by Vexa\n" + sharing
            + "Reply to this email with corrections or questions — I'll update what we hold "
            "and answer here. Or open it and talk it through:")
    # THE SCAFFOLD, not a raw deeplink (PRD §5.5). No share token: this is the organiser's own
    # meeting, and a capability nobody needs is a capability nobody should be handed.
    # BY ROW ID, NEVER BY THE REF (R-B06). `email_attendees` resolves the row two steps later
    # and states the reason in capitals — *"By ROW id, never by (platform, native)"*, the
    # row-97 incident — and `process_meeting` resolves it too. This step, the ONE mail that
    # always sends, was the site that did not: a ref carrying a native id mints a link into a
    # chat that cannot see the meeting the mail is about.
    row = mt.meeting_row(ctx.refs["uid"], ctx.refs.get("meeting_id"), ctx.refs.get("native"))
    row_id = (row or {}).get("id") if isinstance(row, dict) else None
    link = mint_scaffold(
        "post-meeting", organizer, opening="minutes-review",
        meeting_id=row_id or ctx.refs["meeting_id"],
        refs=_scaffold_refs(ctx, ctx.refs["uid"]),
        provenance={"flow": "post_meeting", "step": "email_minutes",
                    "reaction_id": str(getattr(ctx, "reaction_id", "") or ""),
                    "minted_by": str(ctx.refs["uid"])})
    mid = notify(organizer, f"Minutes: {_mail_title(ctx)}", body, link=link)
    mx.register_thread(db, mid, ctx.refs["uid"], f"meet-{ctx.refs['meeting_id']}")
    return Done({"message_id": mid, "link": link}, provider_ref=mid)
```

</ViewSource>

<ViewSource step="email_attendees">

```python
@reg.step(needs=("agent", "meetings"), absent={"agent": "degrade"})
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
    # THE REPORT, OR ITS HONEST ABSENCE — read exactly as `email_minutes` reads it. A skipped
    # `process_meeting` leaves a CONFIRMED receipt carrying `{"outcome": "skipped", …}` rather
    # than no receipt, so `.get` answers "there is no report" for the one case that produces
    # none and still raises for a genuinely missing prior, which is a different bug.
    written = (ctx.prior.get("process_meeting") or {}).get("report")
    if written is None:
        return _mail_the_recording_to_attendees(ctx, who)
    report = _readable(written).strip()
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
    # `behavior/mail/README.md` states for this whole directory: the live text is
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
            "meeting": _mail_title(ctx),
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
        subject = _mail_title(ctx)
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
```

</ViewSource>

<ViewSource step="drop_to_attendees">

```python
@reg.step(needs=("agent", "meetings"), absent={"agent": "skip"})
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

    PER PERSON, four effects and no others:
      1. their platform user (`ensure_platform_user`) and their desk
         (`POST /api/workspace/init` AS THEM — the same seeding the click does). Nothing else
         is built: no chat, no session, no scaffolding.
      2. `kg/entities/meeting/<date>-<slug>.md` — the report, with the meeting's title, date,
         organiser and roster as frontmatter, and their own link at the foot.
      3. `kg/entities/meeting/index.md` gains one line, once.
      4. ONE SHORT-LIST ITEM PER COMMITMENT THE REPORT NAMED FOR THEM (Vexa-ai/vexa#1614) —
         `POST /api/proposals`, so their next empty chat offers the job in one click. Plain
         text matching over the report's own `Committed` section (`commitments_for`), no model,
         and never raising: see the call site.
    The two file writes go through `PUT /api/workspace/file`, which commits, so each lands in
    that desk's history rather than as an untracked file; the short list is a queue, not a fact
    about the workspace, and is git-excluded on the far side.

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
    Effect: N desk writes · Result: {dropped, to, failed, entity, proposed}."""
    pm = ctx.prior.get("process_meeting") or {}
    report = _readable(pm.get("report") or "").strip()
    if not report:
        return Done({"dropped": 0, "to": [], "failed": [],
                     "skipped": "there is no report to drop"})
    uid = ctx.refs["uid"]
    title = _mail_title(ctx)
    # `refs["organizer"]` is absent on an AD HOC meeting (F212, 2026-09-03) — `uid` IS the
    # person then, and `_organizer_address` resolves their own address. The room always
    # includes at least this one desk (decision 22a: "the organiser is not special"), so an
    # unresolvable address is not a meeting with nobody to drop to — it is a broken lookup,
    # and it fails loudly rather than writing an entity whose `organizer:` field, and whose
    # `ensure_platform_user` call below, is the literal placeholder string "the organiser".
    organizer = _organizer_address(ctx)
    if not organizer:
        raise StepError(
            f"cannot drop meeting {ctx.refs.get('meeting_id')!r} to any desk: no organizer on "
            f"the ref and no address for uid {uid!r}.", retryable=False)
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
    native = str(ctx.refs.get("native") or "").strip()
    body = _drop_entity(title=title, day=day, entity_id=entity_id, date_prose=date_prose,
                        organizer=organizer, participants=roster, report=report, link="",
                        meeting_id=str(mid or ""), native=native)
    done = list(ctx.scratch.setdefault("dropped", []))
    failed = list(ctx.scratch.setdefault("drop_failed", []))
    # WHO EACH ADDRESS IS, for the commitment read below — the invite's own names, lower-cased
    # on the address so the lookup matches the roster this step already lower-cases.
    names = {str(k).strip().lower(): str(v or "")
             for k, v in (ctx.refs.get("participant_names") or {}).items()}
    proposed = int(ctx.scratch.get("proposed") or 0)
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
                link=d.get("link") or "", meeting_id=str(mid or ""), native=native),
                # A page this person has already grown keeps everything but its report region
                # (#1598). Their words are not this step's to overwrite.
                report=report)
            _write_if_changed(their_uid, index_path, _index_entry(
                ws_file(their_uid, index_path), title, filename, day))
            # AND WHAT THEY OWE, ONTO THEIR SHORT LIST (Vexa-ai/vexa#1614). The report is the
            # first place an agent sees a job for this person; the empty chat is where they
            # meet it. Never raises and never counts against the drop: a chip that did not
            # appear must not cost somebody the meeting's record.
            proposed += propose_commitments(their_uid, a, names.get(a.lower(), ""),
                                            report, title, mid)
            done.append(a)
            failed = [f for f in failed if not f.startswith(a + ":")]
        except Exception as e:  # noqa: BLE001 — one person never costs the rest theirs
            failed = [f for f in failed if not f.startswith(a + ":")]
            failed.append(f"{a}: {type(e).__name__}: {e}"[:240])
        ctx.scratch["dropped"] = done
        ctx.scratch["drop_failed"] = failed
        ctx.scratch["proposed"] = proposed
        ctx.checkpoint()      # same shape as the fan-out above: N round trips, one lease
    if done:
        return Done({"dropped": len(done), "to": done, "failed": failed,
                     "entity": entity_path, "meeting_id": mid, "proposed": proposed,
                     # every copy is these bytes plus that person's own link
                     "bytes": len(body)})
    raise StepError(
        f"every desk drop failed for meeting {mid} ({len(room)} person(s) in the room): "
        + " · ".join(failed), retryable=True)
```

</ViewSource>
