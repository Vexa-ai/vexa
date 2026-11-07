---
kind: flow
flow: routine_paused
version: 1
trigger: routine.paused
steps: 1
generated: from the code that runs it — edits here are overwritten
---

# routine_paused

Runs when **`routine.paused`** happens, in 1 step. This page is written from the code — the docstrings below are the ones in the image that is running, and the Python at the foot is that code verbatim.

| | |
|---|---|
| **trigger** | `routine.paused` |
| **version** | 1 — a step list changes by adding a version, never by editing one in place |
| **mails** | nothing |
| **rules it honours** | none |

## The steps, in order

### 1. `await_routine`

The PAUSED-ROUTINE card: a scheduled routine the harness switched off after its runs kept being refused the same way (agent-api `control_plane/routine_refusals.py`).

- **reads:** refs.{uid, routine, reason}
- **domains:** without **agent** the reaction ends there, saying so

## The code

Read-only, and the same bytes the image runs. It is here because the founder asked whether we can show it: the page is the explanation, this is the appendix.

<ViewSource step="await_routine">

```python
@reg.step(needs=("agent",))
def await_routine(ctx: StepCtx):
    """The PAUSED-ROUTINE card: a scheduled routine the harness switched off after its runs
    kept being refused the same way (agent-api `control_plane/routine_refusals.py`).

    Same re-read as the two cards above, for the same reason: the person may already have
    switched it back on, edited it or deleted it, and must not be asked again. `Done` when the
    file is gone or no longer says `enabled: false`; otherwise `Block` on the routine's name —
    the sentence around it is `behavior/queue/routine_paused.human.md`'s.
    Reads: refs.{uid, routine, reason}."""
    uid = str(ctx.refs.get("uid") or "").strip()
    name = str(ctx.refs.get("routine") or "").strip()
    if not uid or not name or "/" in name or name in (".", ".."):
        raise StepError("routine.paused needs a uid and a routine name — without both there is "
                        "no routine to look at", retryable=False)
    text = p.ws_file(uid, f"routines/{name}.md") or ""
    if not text or not ROUTINE_OFF.search(text):
        return Done({"routine": name, "outcome": "no_longer_paused"})
    return Block(f"routine {name} paused: {str(ctx.refs.get('reason') or '')}"[:200])
```

</ViewSource>
