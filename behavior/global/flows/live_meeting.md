---
kind: flow
flow: live_meeting
version: 1
trigger: meeting.started
steps: 1
generated: from the code that runs it — edits here are overwritten
---

# live_meeting

Runs when **`meeting.started`** happens, in 1 step. This page is written from the code — the docstrings below are the ones in the image that is running, and the Python at the foot is that code verbatim.

| | |
|---|---|
| **trigger** | `meeting.started` |
| **version** | 1 — a step list changes by adding a version, never by editing one in place |
| **mails** | nothing |
| **rules it honours** | none |

## The steps, in order

### 1. `attend_live`

PENDING WHILE THE CALL RUNS — the reaction that makes "a meeting is happening right now" a queue item instead of a `/bots/status` read at the edge.

- **domains:** reaches no other domain

## The code

Read-only, and the same bytes the image runs. It is here because the founder asked whether we can show it: the page is the explanation, this is the appendix.

<ViewSource step="attend_live">

```python
@reg.step
def attend_live(ctx: StepCtx):
    """PENDING WHILE THE CALL RUNS — the reaction that makes "a meeting is happening right
    now" a queue item instead of a `/bots/status` read at the edge.

    It does nothing and that is the point. `Wait` burns no attempt and costs nothing while
    parked (`flows/loop.tick`), so the value here is entirely in the ROW: a person's queue can
    say a call is live because flows is holding a pending reaction that says so, with the flow
    that produced it attached — which is what `behavior/queue/live_meeting.pending.md` speaks.

    Reaches no domain: `needs` is empty on purpose, so this works in every profile.
    """
    mid = str(ctx.refs.get("meeting_id") or "").strip()
    if not mid:
        raise StepError(
            "meeting.started carried no meeting_id — there is no call to attend, and every "
            "later reader of this reaction identifies the meeting by that field.",
            retryable=False)
    if _completion_seen(mid):
        return Done({"meeting_id": mid, "outcome": "completed"})
    since = ctx.scratch.setdefault("live_since", ctx.clock_now)
    if ctx.clock_now - since > LIVE_CAP_S:
        return Done({"meeting_id": mid, "outcome": "lapsed"})
    return Wait(seconds=LIVE_POLL_S)
```

</ViewSource>
