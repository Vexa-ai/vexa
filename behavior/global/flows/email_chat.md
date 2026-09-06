---
kind: flow
flow: email_chat
version: 1
trigger: mail.reply
steps: 2
generated: from the code that runs it — edits here are overwritten
---

# email_chat

Runs when **`mail.reply`** happens, in 2 steps. This page is written from the code — the docstrings below are the ones in the image that is running, and the Python at the foot is that code verbatim.

| | |
|---|---|
| **trigger** | `mail.reply` |
| **version** | 1 — a step list changes by adding a version, never by editing one in place |
| **mails** | `(composed in the step, from no template)` |
| **rules it honours** | none |

## The steps, in order

### 1. `feedback_turn`

One conversation turn: hand the inbound email to the session's agent (workspace updated where facts changed), collect the reply via the FILE-OUTBOX contract (mail_outbox/<session>.md, content-hash), coalesced across sibling reactions.

- **reads:** refs.{uid,session,text,from_addr}
- **effect:** agent worker turn
- **result:** {reply}
- **domains:** without **agent** the reaction ends there, saying so

### 2. `email_reply`

Mail the agent's reply on the same thread; register Message-ID; record the content hash in mail_outbox_sent (send-once across reactions and restarts). Prior: feedback_turn · Effect: one notification.

- **effect:** one notification
- **domains:** reaches no other domain
- **mails:** `(composed in the step, from no template)`

## The code

Read-only, and the same bytes the image runs. It is here because the founder asked whether we can show it: the page is the explanation, this is the appendix.

<details>
<summary>view source — <code>feedback_turn</code></summary>

```python
@reg.step(needs=("agent",))
def feedback_turn(ctx: StepCtx):
    """One conversation turn: hand the inbound email to the session's agent (workspace
    updated where facts changed), collect the reply via the FILE-OUTBOX contract
    (mail_outbox/<session>.md, content-hash), coalesced across sibling reactions.

    THE MAIL IS DATA, NEVER INSTRUCTIONS — see `_untrusted_mail`. Who is even allowed to reach
    this step is decided one process away, in the mailbox intake's allow-list and rate limits;
    this is the second half of the same rule, for the mail that IS allowed through.

    Reads: refs.{uid,session,text,from_addr} · Effect: agent worker turn · Result: {reply}."""
    uid, session = ctx.refs["uid"], ctx.refs["session"]
    if "dispatched" not in ctx.scratch:
        ctx.scratch["prev_hash"] = p.ag.collect_outbox(uid, session, None)[1]
        p.ag.dispatch_turn(
            uid, session,
            "[email-reply] The participant replied by email. Process it: update the workspace "
            "where it changes facts (feedback on minutes → amend the note; onboarding answers → "
            "continue the discovery loop and remember the .scaffolded acceptance). Then answer "
            f"them. DELIVERY CONTRACT: write your answer to the file mail_outbox/{session}.md "
            "(overwrite fully) — that file is emailed verbatim, plain text."
            + _untrusted_mail(ctx.refs.get("from_addr") or ctx.refs.get("organizer") or "",
                              ctx.refs["text"]),
            # NOBODY TYPED THIS EITHER (Vexa-ai/vexa#1605): the person wrote an EMAIL, and the
            # instruction block wrapped around it is ours. agent-api marks it from these two.
            flow=ctx.reaction.flow, step=ctx.reaction.step)
        ctx.scratch["dispatched"] = True
        return Wait(seconds=10)
    reply, h = p.ag.collect_outbox(uid, session, ctx.scratch.get("prev_hash"))
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
```

</details>

<details>
<summary>view source — <code>email_reply</code></summary>

```python
@reg.step
def email_reply(ctx: StepCtx):
    """Mail the agent's reply on the same thread; register Message-ID; record the content
    hash in mail_outbox_sent (send-once across reactions and restarts).
    Prior: feedback_turn · Effect: one notification."""
    ft = ctx.prior["feedback_turn"]
    if not ft.get("reply"):
        return Done({"coalesced": True})                    # content already mailed by a sibling
    mid = p.notify(ctx.refs["from_addr"], "Re: " + (ctx.refs.get("subject") or "Vexa"),
                   ft["reply"], in_reply_to=ctx.refs.get("orig_msgid"))
    p.mx.register_thread(db, mid, ctx.refs["uid"], ctx.refs["session"])
    db.execute("""INSERT INTO mail_outbox_sent (subject_uid, session, hash, sent_at)
                  VALUES (:u,:s,:h,:t) ON CONFLICT DO NOTHING""",
               {"u": ctx.refs["uid"], "s": ctx.refs["session"],
                "h": ctx.scratch.get("out_hash", ""), "t": ctx.clock_now})
    return Done({"message_id": mid}, provider_ref=mid)
```

</details>
