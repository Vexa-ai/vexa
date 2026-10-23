---
kind: flow
flow: desk_setup
version: 1
trigger: desk.unscaffolded
steps: 1
generated: from the code that runs it — edits here are overwritten
---

# desk_setup

Runs when **`desk.unscaffolded`** happens, in 1 step. This page is written from the code — the docstrings below are the ones in the image that is running, and the Python at the foot is that code verbatim.

| | |
|---|---|
| **trigger** | `desk.unscaffolded` |
| **version** | 1 — a step list changes by adding a version, never by editing one in place |
| **mails** | nothing |
| **rules it honours** | none |

## The steps, in order

### 1. `await_scaffold`

The SETUP card: a desk exists and has never been filled in.

- **reads:** refs.uid
- **domains:** without **agent** the reaction ends there, saying so

## The code

Read-only, and the same bytes the image runs. It is here because the founder asked whether we can show it: the page is the explanation, this is the appendix.

<ViewSource step="await_scaffold">

```python
@reg.step(needs=("agent",))
def await_scaffold(ctx: StepCtx):
    """The SETUP card: a desk exists and has never been filled in. Reads: refs.uid.

    It re-reads the desk rather than trusting the event, because the fact is old the moment it
    is published: a person who finished setup between the publish and this step must not be
    asked again. `Done` when the marker is there, `Block` when it is not.
    """
    uid = str(ctx.refs.get("uid") or "").strip()
    if not uid:
        raise StepError("desk.unscaffolded carried no uid — there is no desk to look at",
                        retryable=False)
    if p.scaffolded(uid, str(ctx.refs.get("slug") or "") or None):
        return Done({"uid": uid, "outcome": "already_scaffolded"})
    return Block("desk not scaffolded")
```

</ViewSource>
