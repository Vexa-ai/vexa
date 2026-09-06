---
kind: flow
flow: invite_intake
version: 3
trigger: invite.received
steps: 9
generated: from the code that runs it — edits here are overwritten
---

# invite_intake

Runs when **`invite.received`** happens, in 9 steps. This page is written from the code — the docstrings below are the ones in the image that is running, and the Python at the foot is that code verbatim.

| | |
|---|---|
| **trigger** | `invite.received` |
| **version** | 3 — a step list changes by adding a version, never by editing one in place |
| **mails** | `(composed in the step, from no template)` |
| **rules it honours** | none |

## The steps, in order

### 1. `ensure_user`

Provision the platform user for the organizer (idempotent lookup-or-create).

- **reads:** refs.organizer
- **effect:** admin-api user (+scoped token minted per later call)
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
- **mails:** `(composed in the step, from no template)`

### 4. `emit_prep`

EMIT meeting.upcoming — the fact the prepare flow reacts to.

- **domains:** reaches no other domain

### 5. `await_start`

Sleep until start − 2 min — time is a column (Wait until), zero cost while parked.

- **reads:** refs.start
- **domains:** without **meetings** the reaction ends there, saying so

### 6. `dispatch_bot`

Spawn the REAL bot via gateway POST /bots (transcribe per deployment; 409 = adopt the existing meeting). Prior: ensure_user (for the key) · Effect: bot container Result: {meeting_id, native, platform}.

- **effect:** bot container
- **result:** {meeting_id, native, platform}
- **domains:** without **meetings** the reaction ends there, saying so

### 7. `emit_started`

EMIT meeting.started — the fact the live flow reacts to. Prior: dispatch_bot.

- **domains:** reaches no other domain

### 8. `run_meeting`

Poll-composite until completed, over the transcribe window.

- **domains:** without **meetings** the reaction ends there, saying so

### 9. `emit_completed`

EMIT meeting.completed carrying IDENTITY ONLY — the fact the post-meeting flows react to. Prior: dispatch_bot, run_meeting.

- **domains:** reaches no other domain

## The code

Read-only, and the same bytes the image runs. It is here because the founder asked whether we can show it: the page is the explanation, this is the appendix.

<details>
<summary>view source — <code>ensure_user</code></summary>

```python
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
```

</details>

<details>
<summary>view source — <code>rsvp_accept</code></summary>

```python
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
```

</details>

<details>
<summary>view source — <code>ack_by_email</code></summary>

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
    body = (f"Vexa accepted the invitation and joins {ctx.refs['url']} at "
            f"{_their_clock(uid, ctx.refs['start'])}.")
    if not ready:
        body += ("\n\nOne thing before your minutes can flow: your workspace isn't set up yet — "
                 "answer the setup email that follows (it's a short conversation, not a form).")
    mid = notify(ctx.refs["organizer"], f"Vexa will join: {ctx.refs['title']}", body)
    # the ack is a thread anchor too: replying to the meeting confirmation is a conversation
    mx.register_thread(db, mid, uid, "main" if ready else "onboarding")
    return Done({"message_id": mid, "workspace_ready": ready}, provider_ref=mid)
```

</details>

<details>
<summary>view source — <code>emit_prep</code></summary>

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
    ctx.emit(UPCOMING.name, f"prep-{ctx.refs['ics_uid']}",
             {**ctx.refs, "uid": ctx.prior["ensure_user"]["uid"]})
    return Done({})
```

</details>

<details>
<summary>view source — <code>await_start</code></summary>

```python
def await_start(ctx: StepCtx):
    """Sleep until start − 2 min — time is a column (Wait until), zero cost while parked.
    Reads: refs.start."""
    if ctx.clock_now < ctx.refs["start"] - 120:
        return Wait(until=ctx.refs["start"] - 120)
    return Done({})
```

</details>

<details>
<summary>view source — <code>dispatch_bot</code></summary>

```python
def dispatch_bot(ctx: StepCtx):
    """Spawn the REAL bot via gateway POST /bots (transcribe per deployment; 409 = adopt the
    existing meeting). Prior: ensure_user (for the key) · Effect: bot container
    Result: {meeting_id, native, platform}."""
    uid = ctx.prior["ensure_user"]["uid"]
    key = user_api_key(uid)
    # transcribe_enabled is NOT passed. It used to be hardcoded False here, against this step's
    # own docstring, which is why a standup recorded audio and captured no words. The platform
    # defaults it to True and resolves TRANSCRIBE_ENABLED per deployment — so naming it here
    # could only ever override a correct decision with a worse one. The name is the person's;
    # whether we transcribe at all is the deployment's.
    # NO bot_name. The person's default is a fact about the BOT, so the domain that owns the bot
    # resolves it — meeting-api reads it from identity's bot-context on every spawn path now. This
    # step used to read a `bot_name` key out of `.settings.json` in the AGENT domain, which is how
    # one fact came to have three stores and why the same person's bot showed up under one name
    # when a calendar armed it and another when a flow did (founder ruling, 2026-09-02).
    st, body = http("POST", f"{meetings_door()}/bots", {"X-API-Key": key},
                    {"meeting_url": ctx.refs["url"]})
    if st == 409:
        st2, existing = http("GET", f"{meetings_door()}/bots", {"X-API-Key": key})
        rows = existing if isinstance(existing, list) else existing.get("meetings", [])
        for m in rows:
            if m.get("native_meeting_id") == ctx.refs["url"].rsplit("/", 1)[1]:
                return Done({"meeting_id": m["id"], "native": m["native_meeting_id"],
                             "platform": m.get("platform", "google_meet")},
                            provider_ref=str(m["id"]))
        raise StepError(f"409 but meeting not found")
    if st not in (200, 201):
        raise StepError(f"spawn failed {st}: {str(body)[:120]}")
    return Done({"meeting_id": body["id"],
                 "native": body.get("native_meeting_id") or ctx.refs["url"].rsplit("/", 1)[1],
                 "platform": body.get("platform", "google_meet")},
                provider_ref=str(body["id"]))
```

</details>

<details>
<summary>view source — <code>emit_started</code></summary>

```python
@reg.step
def emit_started(ctx: StepCtx):
    """EMIT meeting.started — the fact the live flow reacts to. Prior: dispatch_bot.

    THE PRODUCER, on this deployment, and deliberately a temporary one. meeting-api already
    derives a typed `meeting.started` when the bot goes ACTIVE
    (`core/meetings/services/meeting-api/src/meeting_api/lifecycle/webhook.py:40`) — that is
    the true signal and the right home for it. Nothing carries it into flows' intake, so the
    event exists and nothing reacts to it.

    Emitting it from inside `invite_intake` is the shape `emit_prep` already uses one screen
    up, for the same stated reason: a second producer can admit the same event type later
    WITHOUT touching this step, because admission dedups on the source event id. When meetings
    publishes it, this step goes and the flow does not change.
    """
    d = ctx.prior["dispatch_bot"]
    refs = {k: v for k, v in ctx.refs.items() if k != "transcript"}
    ctx.emit(STARTED.name, f"live-{d['meeting_id']}",
             {**refs, "meeting_id": d["meeting_id"], "native": d["native"],
              "uid": ctx.prior["ensure_user"]["uid"]})
    return Done({})
```

</details>

<details>
<summary>view source — <code>run_meeting</code></summary>

```python
def run_meeting(ctx: StepCtx):
    """Poll-composite until completed, over the transcribe window."""
    d = ctx.prior["dispatch_bot"]
    m = _status(ctx)
    s = m.get("status") or "?"
    if s == STATUS_UNREADABLE:
        # THE READ ITSELF FAILED, and that is not a meeting state. Bounded, because a 404 on a
        # meeting this key cannot address never becomes readable by asking again — and unbounded
        # retries against it are exactly how a reaction disappears into `retrying` with a reason
        # that names no cause.
        n = int(ctx.scratch.get("status_unreadable") or 0) + 1
        ctx.scratch["status_unreadable"] = n
        if n > STATUS_READ_ATTEMPTS_MAX:
            raise StepError(
                f"the bot's status has been unreadable {n} times for "
                f"{d['platform']}/{d['native']}: {m.get('http')} — {m.get('detail')}",
                retryable=False)
        return Wait(seconds=6)
    ctx.scratch["status_unreadable"] = 0
    if s in ("requested", "joining", "awaiting_admission"):
        return Wait(seconds=6)
    if s == "active":
        window = ctx.refs.get("transcribe_s", 45.0)
        # The window is measured from the meeting's own start rather than from the moment the bot
        # went active: this step is stateless across ticks by design, and the start is a ref.
        if ctx.clock_now - ctx.refs["start"] < window:
            return Wait(seconds=8)
        failures = _stop_bot(ctx, d)
        if failures and failures >= STOP_ATTEMPTS_MAX:
            return Block(
                reason=(f"the bot for {d['platform']}/{d['native']} would not stop after "
                        f"{failures} attempts: {ctx.scratch.get('stop_last')}. It may still be in "
                        f"the call and recording."),
                deadline_s=STOP_DEADLINE_S)
        return Wait(seconds=5)
    if s == "stopping":
        # A CEILING ON THE STOP. Without one this is `Wait(4)` for the life of the worker: the
        # meeting never completes, the reaction never fails, and the bot that will not leave the
        # call is visible to nobody. The deadline turns it into a blocked row with a reason, which
        # is the surface a person actually reads.
        since = ctx.scratch.setdefault("stopping_since", ctx.clock_now)
        if ctx.clock_now - since > STOP_DEADLINE_S:
            return Block(
                reason=(f"the bot for {d['platform']}/{d['native']} has been stopping for "
                        f"{int(ctx.clock_now - since)}s and has not left the call."),
                deadline_s=STOP_DEADLINE_S)
        return Wait(seconds=4)
    if s == "completed":
        segs = m.get("segments") or []
        # The transcript is NOT returned. It used to come back capped at 8,000 characters so it
        # could ride inside meeting.completed — a copy of a fact the transcription domain owns,
        # and a cap that decided how much of an hour the agent would ever see. Segment count is a
        # receipt; the words are read through the MCP by whoever needs them.
        return Done({"segments": len(segs)})
    if s == "failed":
        raise StepError(f"meeting failed: {m.get('completion_reason')}", retryable=False)
    return Wait(seconds=6)
```

</details>

<details>
<summary>view source — <code>emit_completed</code></summary>

```python
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
```

</details>
