---
kind: flow
flow: invite_join
version: 1
trigger: invite.*
steps: 5
generated: from the code that runs it — edits here are overwritten
---

# invite_join

Runs when **`invite.*`** happens, in 5 steps. This page is written from the code — the docstrings below are the ones in the image that is running, and the Python at the foot is that code verbatim.

| | |
|---|---|
| **trigger** | `invite.*` |
| **version** | 1 — a step list changes by adding a version, never by editing one in place |
| **mails** | nothing |
| **rules it honours** | none |

## The steps, in order

### 1. `ensure_user`

Provision the platform user for the organizer (idempotent lookup-or-create).

- **reads:** refs.organizer
- **effect:** admin-api user (the subject of later service-door calls)
- **result:** {uid} — every later step's identity
- **domains:** reaches no other domain

### 2. `dispatch_bot`

Spawn the REAL bot via gateway POST /bots (transcribe per deployment; 409 = adopt the existing meeting). Prior: ensure_user (the subject) · Effect: bot container Result: {meeting_id, native, platform}.

- **effect:** bot container
- **result:** {meeting_id, native, platform}
- **domains:** without **meetings** the reaction ends there, saying so

### 3. `emit_started`

EMIT meeting.started — the fact the live flow reacts to. Prior: dispatch_bot.

- **domains:** reaches no other domain

### 4. `run_meeting`

Poll-composite until completed: the bot stays until the meeting ends, unless `refs.transcribe_s` caps its window.

- **domains:** without **meetings** the reaction ends there, saying so

### 5. `emit_completed`

EMIT meeting.completed carrying IDENTITY ONLY — the fact the post-meeting flows react to. Prior: dispatch_bot, run_meeting.

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

<ViewSource step="dispatch_bot">

```python
def dispatch_bot(ctx: StepCtx):
    """Spawn the REAL bot via gateway POST /bots (transcribe per deployment; 409 = adopt the
    existing meeting). Prior: ensure_user (the subject) · Effect: bot container
    Result: {meeting_id, native, platform}."""
    uid = ctx.prior["ensure_user"]["uid"]
    hdr = door_headers(uid)
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
    # THE MEETING GOES INTO THE INVITE'S WORKSPACE (founder 2026-10-14): the invite sensor reads the
    # workspace link out of the event body and the invite carries it as `workspace`. meeting-api binds
    # it only when the organizer is a member (`workspace_id`, authorized against the signed
    # memberships); refused, the bot still goes, to the organizer's default, and the reason is kept.
    ws = str(ctx.refs.get("workspace") or ctx.refs.get("workspace_id") or "").strip()
    st, body = http("POST", f"{meetings_door()}/bots", hdr,
                    {"meeting_url": ctx.refs["url"], **({"workspace_id": ws} if ws else {})})
    if ws and st in (400, 403, 422):
        ctx.scratch["workspace_refused"] = f"{st}: {_http_detail(body)}"[:200]
        st, body = http("POST", f"{meetings_door()}/bots", hdr,
                        {"meeting_url": ctx.refs["url"]})
    if st == 409:
        st2, existing = http("GET", f"{meetings_door()}/bots", hdr)
        rows = existing if isinstance(existing, list) else existing.get("meetings", [])
        for m in rows:
            if m.get("native_meeting_id") == _native_of(ctx.refs["url"]):
                return Done({"meeting_id": m["id"], "native": m["native_meeting_id"],
                             "platform": m.get("platform", "google_meet")},
                            provider_ref=str(m["id"]))
        raise StepError(f"409 but meeting not found")
    if st not in (200, 201):
        raise StepError(f"spawn failed {st}: {redact_urls_in(str(body))[:120]}")
    return Done({"meeting_id": body["id"],
                 "native": body.get("native_meeting_id") or _native_of(ctx.refs["url"]),
                 "platform": body.get("platform", "google_meet")},
                provider_ref=str(body["id"]))
```

</ViewSource>

<ViewSource step="emit_started">

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

</ViewSource>

<ViewSource step="run_meeting">

```python
def run_meeting(ctx: StepCtx):
    """Poll-composite until completed: the bot stays until the meeting ends, unless
    `refs.transcribe_s` caps its window."""
    d = ctx.prior["dispatch_bot"]
    try:
        m = _status(ctx)
    except MeetingsUnavailable as e:
        # The engine waits on it, and the run's own ceiling still bounds the wait.
        if _ran(ctx) > ACTIVE_CEILING_S:
            return Block(
                reason=(f"the bot's status for {d['platform']}/{d['native']} could not be read "
                        f"{int(_ran(ctx))}s after the meeting's start ({e}), past the bot's own "
                        f"{BOT_MAX_ACTIVE_S // 3600} h backstop and a 30 min grace."),
                deadline_s=STOP_DEADLINE_S)
        raise
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
        # NO WINDOW, NO STOP (brain:card/d7faa9c3946c). The window is a REHEARSAL's cap: the first
        # harness ran a real bot for 45 s and then stopped it. No invite carries `transcribe_s`, so
        # reading its absence as 45 s asked every invited bot to leave 45 s after the start. A bot
        # started any other way stays until the meeting ends and leaves on its own; so does this one.
        window = ctx.refs.get("transcribe_s")
        if window is None:
            # A CEILING ON THE POLL, the same kind `stopping` has: a Wait spends no attempt, so a
            # bot whose status never moves would otherwise be polled for the life of the worker.
            if _ran(ctx) > ACTIVE_CEILING_S:
                return Block(
                    reason=(f"the bot for {d['platform']}/{d['native']} still reported active "
                            f"{int(_ran(ctx))}s after the meeting's start, past its own "
                            f"{BOT_MAX_ACTIVE_S // 3600} h backstop and a 30 min grace, and never "
                            f"reported the meeting ended. Flows did not ask it to leave."),
                    deadline_s=STOP_DEADLINE_S)
            return Wait(seconds=8)
        # The window is measured from the meeting's own start rather than from the moment the bot
        # went active: this step is stateless across ticks by design, and the start is a ref.
        if ctx.clock_now - ctx.refs["start"] < float(window):
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

</ViewSource>

<ViewSource step="emit_completed">

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

</ViewSource>
