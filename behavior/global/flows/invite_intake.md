---
kind: flow
flow: invite_intake
version: 4
trigger: invite.received
steps: 4
generated: from the code that runs it — edits here are overwritten
---

# invite_intake

Runs when **`invite.received`** happens, in 4 steps. This page is written from the code — the docstrings below are the ones in the image that is running, and the Python at the foot is that code verbatim.

| | |
|---|---|
| **trigger** | `invite.received` |
| **version** | 4 — a step list changes by adding a version, never by editing one in place |
| **mails** | `bot-scheduled` |
| **rules it honours** | none |

## The steps, in order

### 1. `ensure_user`

Provision the platform user for the organizer (idempotent lookup-or-create).

- **reads:** refs.organizer
- **effect:** admin-api user (the subject of later service-door calls)
- **result:** {uid} — every later step's identity
- **domains:** reaches no other domain

### 2. `rsvp_accept`

Accept the invitation IN THE ORGANIZER'S CALENDAR — iMIP METHOD:REPLY over SMTP; Google flips Vexa to "Yes" in the guest list.

- **reads:** refs.{organizer,ics_uid,start,title}
- **effect:** one calendar reply email
- **result:** {message_id}
- **domains:** reaches no other domain

### 3. `ack_by_email`

Acknowledge by email: when Vexa joins, plus the finalize-your-workspace ask when onboarding is pending. Registers the mail as a THREAD ANCHOR (replies become conversation).

- **reads:** refs.{organizer,url,start,title} · Prior: ensure_user
- **effect:** one notification
- **result:** {message_id, workspace_ready}
- **domains:** without **agent** this step is skipped and the flow carries on
- **mails:** `bot-scheduled`

### 4. `emit_prep`

EMIT meeting.upcoming — the fact the prepare flow reacts to.

- **domains:** reaches no other domain

## The code

Read-only, and the same bytes the image runs. It is here because the founder asked whether we can show it: the page is the explanation, this is the appendix.

<ViewSource step="ensure_user">

```python
@reg.step
def ensure_user(ctx: StepCtx):
    """Provision the platform user for the organizer (idempotent lookup-or-create).
    Reads: refs.organizer · Effect: admin-api user (the subject of later service-door calls)
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
```

</ViewSource>

<ViewSource step="rsvp_accept">

```python
@reg.step
def rsvp_accept(ctx: StepCtx):
    """Accept the invitation IN THE ORGANIZER'S CALENDAR — iMIP METHOD:REPLY over SMTP;
    Google flips Vexa to "Yes" in the guest list. Reads: refs.{organizer,ics_uid,start,title}
    Effect: one calendar reply email · Result: {message_id}."""
    uid = (ctx.prior.get("ensure_user") or {}).get("uid")
    if uid and not setting(uid, "mail_rsvp"):
        return Done({"skipped": "mail_rsvp is off for this person"})
    out = outbound.send(
        "rsvp", ctx.refs["organizer"], uid=str(uid or ""), subject="Accepted", body="", db=db,
        sender=lambda to, *_a, **_k: mx.send_rsvp_accept(
            to, ics_uid=ctx.refs["ics_uid"], start_epoch=ctx.refs["start"],
            title=ctx.refs["title"]))
    if not out.sent:
        return Done({"skipped": f"rsvp not sent: {out.result} ({out.detail})"})
    mid = out.message_id
    return Done({"message_id": mid}, provider_ref=mid)
```

</ViewSource>

<ViewSource step="ack_by_email">

```python
@reg.step(needs=("agent",), absent="skip")
def ack_by_email(ctx: StepCtx):
    """Acknowledge by email: when Vexa joins, plus the finalize-your-workspace ask when
    onboarding is pending. Registers the mail as a THREAD ANCHOR (replies become conversation).
    Reads: refs.{organizer,url,start,title} · Prior: ensure_user · Effect: one notification
    Result: {message_id, workspace_ready}."""
    uid = ctx.prior["ensure_user"]["uid"]
    if not setting(uid, "mail_join"):
        return Done({"skipped": "mail_join is off for this person"})
    ready = scaffolded(uid)
    subject, body = mailtext.render("bot-scheduled", uid, {
        "title": ctx.refs["title"], "url": ctx.refs["url"],
        "when": _their_clock(uid, ctx.refs["start"])})
    if not ready:
        body += ("\n\nOne thing before your minutes can flow: your workspace isn't set up yet — "
                 "answer the setup email that follows (it's a short conversation, not a form).")
    mid = mail("bot-scheduled", ctx.refs["organizer"],
               subject or f"Vexa will join: {ctx.refs['title']}", body, db=db, uid=uid)
    if mid is None:
        return Done({"skipped": "mail.bot-scheduled is off"})
    # the ack is a thread anchor too: replying to the meeting confirmation is a conversation
    mx.register_thread(db, mid, uid, "main" if ready else "onboarding")
    return Done({"message_id": mid, "workspace_ready": ready}, provider_ref=mid)
```

</ViewSource>

<ViewSource step="emit_prep">

```python
@reg.step
def emit_prep(ctx: StepCtx):
    """EMIT meeting.upcoming — the fact the prepare flow reacts to.

    THE ADMIT: on this deployment an invite IS the meeting-created event. Nothing else
    publishes one — mailbox.py admits only invite.received and mail.reply, and a meeting made
    any other way (the terminal, the control MCP's bot_schedule, calendar sync) reaches the
    platform's meetings table without telling flows. So the fact is emitted from inside
    invite_intake, before await_start parks: a second producer can admit the same event type
    later without touching this step. Prior: ensure_user."""
    uid = ctx.prior["ensure_user"]["uid"]
    ctx.emit(UPCOMING.name, f"prep-{ctx.refs['ics_uid']}", {**ctx.refs, "uid": uid})
    # The owner's bell says the bot is set (#1783): no meeting row exists yet, so the notice
    # names the invite and opens nothing of its own. An echo — it never fails the intake.
    _common.notify_in_app("bot_scheduled", to=str(uid), meeting_id=ctx.refs["ics_uid"],
                          title=str(ctx.refs.get("title") or ""), object_kind="none")
    return Done({})
```

</ViewSource>
