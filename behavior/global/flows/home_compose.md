---
kind: flow
flow: home_compose
version: 1
trigger: home.stale
steps: 1
generated: from the code that runs it — edits here are overwritten
---

# home_compose

Runs when **`home.stale`** happens, in 1 step. This page is written from the code — the docstrings below are the ones in the image that is running, and the Python at the foot is that code verbatim.

| | |
|---|---|
| **trigger** | `home.stale` |
| **version** | 1 — a step list changes by adding a version, never by editing one in place |
| **mails** | nothing |
| **rules it honours** | none |

## The steps, in order

### 1. `compose_home`

COMPOSE THIS PERSON'S NEW-CHAT HOME NOW — agent-api collects the candidates, ranks them with at most one call to the cheapest configured model, and appends the composition.

- **reads:** refs.{uid|subject}
- **effect:** one home composition appended
- **result:** {id, stage, cards, model} or {skipped}
- **domains:** without **agent** the reaction ends there, saying so

## The code

Read-only, and the same bytes the image runs. It is here because the founder asked whether we can show it: the page is the explanation, this is the appendix.

<ViewSource step="compose_home">

```python
@reg.step(needs=("agent",))
def compose_home(ctx: StepCtx):
    """COMPOSE THIS PERSON'S NEW-CHAT HOME NOW — agent-api collects the candidates, ranks them
    with at most one call to the cheapest configured model, and appends the composition.

    Debounced on agent-api's side (ten minutes; one after a meeting ended), so a burst of
    facts costs one composition; a skipped compose is `Done` with `skipped`, and so is one
    agent-api did not answer — the home is best-effort and never a waiting item.
    Reads: refs.{uid|subject} · Effect: one home composition appended · Result: {id, stage,
    cards, model} or {skipped}."""
    uid = str(ctx.refs.get("uid") or ctx.refs.get("subject") or "").strip()
    if not uid:
        raise StepError("the home fact carried no person — there is no home to compose", retryable=False)
    trigger = getattr(ctx.flow, "on", None)
    try:
        out = p.ag.compose_home(uid, getattr(trigger, "name", "") or "home.stale")
    except StepError as e:
        # THE HOME IS BEST-EFFORT: a composition that could not run is not a waiting item, and
        # must never hold up the meeting's own reactions. The next stale read asks again.
        return Done({"skipped": "agent-api did not answer", "detail": str(e)[:200]})
    return Done({k: out.get(k) for k in ("id", "stage", "cards", "model", "skipped") if k in out})
```

</ViewSource>
