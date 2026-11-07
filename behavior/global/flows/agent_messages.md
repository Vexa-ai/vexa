---
kind: flow
flow: agent_messages
version: 1
trigger: thread.message
steps: 1
generated: from the code that runs it — edits here are overwritten
---

# agent_messages

Runs when **`thread.message`** happens, in 1 step. This page is written from the code — the docstrings below are the ones in the image that is running, and the Python at the foot is that code verbatim.

| | |
|---|---|
| **trigger** | `thread.message` |
| **version** | 1 — a step list changes by adding a version, never by editing one in place |
| **mails** | nothing |
| **rules it honours** | none |

## The steps, in order

### 1. `deliver_message`

Deliver one agent's message into another agent's chat thread as a queued turn, marked as from the sending agent (its thread, and its person or routine) and never as the person's words; waits while the thread is busy. Result: {thread, uid}.

- **result:** {thread, uid}
- **domains:** without **agent** the reaction ends there, saying so

## The code

Read-only, and the same bytes the image runs. It is here because the founder asked whether we can show it: the page is the explanation, this is the appendix.

<ViewSource step="deliver_message">

```python
@reg.step(needs=("agent",))
def deliver_message(ctx: StepCtx):
    """Deliver one agent's message into another agent's chat thread as a queued turn, marked as
    from the sending agent (its thread, and its person or routine) and never as the person's
    words; waits while the thread is busy. Result: {thread, uid}."""
    if ctx.scratch.get("delivered"):
        return Done(ctx.scratch["delivered"])
    uid, thread = str(ctx.refs.get("uid") or ""), str(ctx.refs.get("thread") or "")
    if not uid or not thread or not str(ctx.refs.get("text") or "").strip():
        raise StepError("thread.message carried no uid, thread or text", retryable=False)
    if thread_busy(uid, thread):
        return Wait(seconds=BUSY_WAIT_S)
    code, out = post_message(ctx.refs, turn_id=ctx.reaction_id)
    code = int(code or 0)
    if code == 409:
        return Wait(seconds=BUSY_WAIT_S)
    if not 200 <= code < 300:
        raise StepError(f"agent-api answered {code} to a delivered message — {str(out)[:200]}",
                        retryable=code == 429 or code >= 500)
    delivered = {"thread": thread, "uid": uid}
    ctx.scratch["delivered"] = delivered
    return Done(delivered, provider_ref=thread)
```

</ViewSource>
