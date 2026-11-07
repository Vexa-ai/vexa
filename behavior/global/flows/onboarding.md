---
kind: flow
flow: onboarding
version: 1
trigger: onboarding.completed
steps: 1
generated: from the code that runs it — edits here are overwritten
---

# onboarding

Runs when **`onboarding.completed`** happens, in 1 step. This page is written from the code — the docstrings below are the ones in the image that is running, and the Python at the foot is that code verbatim.

| | |
|---|---|
| **trigger** | `onboarding.completed` |
| **version** | 1 — a step list changes by adding a version, never by editing one in place |
| **mails** | nothing |
| **rules it honours** | none |

## The steps, in order

### 1. `first_meeting`

PENDING UNTIL THIS PERSON HAS SEEN VEXA TRANSCRIBE SOMETHING — the queue row that is a new person's first step.

- **reads:** refs.{subject|uid} · Reaches: meetings (segment count)
- **result:** {meeting_id, segments}
- **domains:** without **meetings** the reaction ends there, saying so

## The code

Read-only, and the same bytes the image runs. It is here because the founder asked whether we can show it: the page is the explanation, this is the appendix.

<ViewSource step="first_meeting">

```python
@reg.step(needs=("meetings",))
def first_meeting(ctx: StepCtx):
    """PENDING UNTIL THIS PERSON HAS SEEN VEXA TRANSCRIBE SOMETHING — the queue row that is a
    new person's first step.

    It does nothing, like `attend_live`, and for the same reason: the value is the ROW. While
    it is pending, `whats_waiting` carries `behavior/queue/onboarding.pending.md`, which tells
    the person's agent to offer them a meeting now — meet.new, `request_meeting_bot`, admit the
    bot, watch the words arrive. When their first transcribed meeting lands, the row completes
    and the item disappears without anybody dismissing it, which is the property the founder
    asked for: *"the queue advances as they go"*.

    THERE IS NO CAP, unlike the live-call reaction. `attend_live` bounds itself because a
    `meeting.completed` that never arrives would park it forever against a call that is long
    over; here the thing being waited for is the person themselves, and an item that expired
    would take the offer away from exactly the people who have not taken it up yet.

    THE INTERVAL IS BOUNDED EVEN THOUGH THE ITEM IS NOT (R-B3), and the two are different
    questions. This looked every thirty seconds forever, per person who had never activated:
    a 200-row scan plus a transcript read, every half-minute, for the life of every dormant
    account. Thirty seconds is right in the minutes after somebody signs up and meaningless on
    day nine, so the wait doubles to fifteen minutes (`onboarding_backoff`) — the offer stays
    open, the cost stops growing with the number of people who have not taken it yet.

    The look count lives in the reaction's own durable scratch, so a worker restart resumes at
    the interval this reaction had reached rather than starting the ramp again.

    Reads: refs.{subject|uid} · Reaches: meetings (segment count) · Result:
    {meeting_id, segments}."""
    uid = _subject_uid(ctx.refs)
    if not uid:
        raise StepError(
            "onboarding.completed carried no subject — there is nobody to onboard, and every "
            "reader of this reaction identifies the person by that field.", retryable=False)
    seen = ctx.scratch.setdefault("silent_meetings", {})
    hit = _transcribed_completion(uid, seen)
    if hit is None:
        looks = int(ctx.scratch.get("looks") or 0)
        ctx.scratch["looks"] = looks + 1
        return Wait(seconds=onboarding_backoff(looks))
    mid, segments = hit
    return Done({"meeting_id": mid, "segments": segments, "activated": True})
```

</ViewSource>
