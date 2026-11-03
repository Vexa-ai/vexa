---
kind: flow
flow: booked_bot
version: 1
trigger: meeting.planned.changed
steps: 1
generated: from the code that runs it — edits here are overwritten
---

# booked_bot

Runs when **`meeting.planned.changed`** happens, in 1 step. This page is written from the code — the docstrings below are the ones in the image that is running, and the Python at the foot is that code verbatim.

| | |
|---|---|
| **trigger** | `meeting.planned.changed` |
| **version** | 1 — a step list changes by adding a version, never by editing one in place |
| **mails** | nothing |
| **rules it honours** | none |

## The steps, in order

### 1. `start_booked_bot`

THE BOOKED BOT (ADR-0052 §5, §6). At `pod_at` the keyed schedule fires this with the `meeting.planned.changed` fact's refs; it asks meeting-api to start the bot for THAT planned row — `POST /internal/meetings/{meeting_id}/start` through the service door, as the row's owner — never by URL. meeting-api re-checks the row under its lock, so a cancel that landed first wins whatever is still armed here.

- **reads:** refs.meeting_id, refs.uid, refs.deadline
- **result:** {meeting_id, code, http, join_at?}
- **domains:** without **meetings** the reaction ends there, saying so

## The code

Read-only, and the same bytes the image runs. It is here because the founder asked whether we can show it: the page is the explanation, this is the appendix.

<ViewSource step="start_booked_bot">

```python
def start_booked_bot(ctx: StepCtx):
    """THE BOOKED BOT (ADR-0052 §5, §6). At `pod_at` the keyed schedule fires this with the
    `meeting.planned.changed` fact's refs; it asks meeting-api to start the bot for THAT planned row
    — `POST /internal/meetings/{meeting_id}/start` through the service door, as the row's owner —
    never by URL. meeting-api re-checks the row under its lock, so a cancel that landed first wins
    whatever is still armed here.

    * 201 `started` → done (the pod waits in the meeting-api row's `requested` until `join_at`);
    * 409 `not_scheduled` / `past_deadline` / `already_live`, 404 → done, nothing to do;
    * 409 `too_early`, 429, 503, 402, a transport error → wait `Retry-After` (default 15 s) and call
      again, until the fact's `deadline` (start + `BOOKED_GRACE_S`); past it → done, with the last
      reason meeting-api gave (it is also stamped on the row, `auto_join_error`);
    * 403 `blocked`, 422 `meeting_host_refused` → done with that reason (asking again changes nothing);
    * the gateway's service door refusing the call ITSELF (its body says ``service door: <reason>``:
      a wrong or rotated service key, the route off its allow-list, an unknown subject, meetings not
      deployed) → the step FAILS (not retryable). meeting-api never saw the call, so nothing is
      stamped on the row and nothing else would ever say why the bot did not come; a configuration
      fault is loud, never "nothing to do".

    Reads: refs.meeting_id, refs.uid, refs.deadline. Result: {meeting_id, code, http, join_at?}."""
    from flows import triggers

    refs = ctx.refs
    mid, uid = refs.get("meeting_id"), str(refs.get("uid") or "").strip()
    if not isinstance(mid, int) and not str(mid or "").isdigit() or not uid.isdigit():
        raise StepError("start_booked_bot needs the fact's meeting_id and uid", retryable=False)
    mid = int(mid)
    deadline = triggers.as_epoch(refs.get("deadline"))
    last = ctx.scratch.get("last") or {}
    if deadline is not None and ctx.clock_now > deadline:
        return Done({"meeting_id": mid, "code": "past_deadline", "http": last.get("http"),
                     "reason": last.get("detail") or "the meeting's start window passed"})
    got: dict = {}
    try:
        st, body = http("POST", f"{meetings_door()}/internal/meetings/{mid}/start",
                        door_headers(uid), None, response_headers=got)
    except StepError as e:
        if not e.retryable:
            raise
        ctx.scratch["last"] = {"http": None, "detail": str(e)[:300]}
        return _booked_wait(ctx, deadline, BOOKED_RETRY_S)
    st = int(st or 0)
    code = str(body.get("code") or "") if isinstance(body, dict) else ""
    detail = str(body.get("detail") or "")[:300] if isinstance(body, dict) else str(body)[:300]
    ctx.scratch["last"] = {"http": st, "code": code, "detail": detail}
    if not 200 <= st < 300 and detail.startswith(DOOR_REFUSAL):
        raise StepError(f"the service door refused the start of meeting {mid} ({st}: {detail})",
                        retryable=False)
    if st == 201:
        return Done({"meeting_id": mid, "code": "started", "http": st,
                     "join_at": body.get("join_at") if isinstance(body, dict) else None},
                    provider_ref=str(mid))
    if code in BOOKED_DONE or st == 404:
        return Done({"meeting_id": mid, "code": code or f"http-{st}", "http": st,
                     "reason": detail})
    if code in BOOKED_FINAL or st in (401, 403, 422):
        return Done({"meeting_id": mid, "code": code or f"http-{st}", "http": st,
                     "reason": detail})
    # too_early, a full ceiling, no credit yet, an unavailable identity/authority/STT/runtime, any
    # other answer: come back when meeting-api says, until the deadline.
    return _booked_wait(ctx, deadline, _retry_after(got, body))
```

</ViewSource>
