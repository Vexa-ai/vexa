---
kind: flow
flow: friction_fix
version: 1
trigger: friction.fixed
steps: 1
generated: from the code that runs it — edits here are overwritten
---

# friction_fix

Runs when **`friction.fixed`** happens, in 1 step. This page is written from the code — the docstrings below are the ones in the image that is running, and the Python at the foot is that code verbatim.

| | |
|---|---|
| **trigger** | `friction.fixed` |
| **version** | 1 — a step list changes by adding a version, never by editing one in place |
| **mails** | nothing |
| **rules it honours** | none |

## The steps, in order

### 1. `record_friction_fixed`

#1510's C3 — the same reasoning as `record_friction`, one door over: `POST /friction/{id}/fix` calls `admit()` directly, and this step exists only so THAT admission creates a reaction row `flows_timeline.friction_for_subject` can fold into its `status` field. No mail, no desk card, no downstream effect.

- **domains:** reaches no other domain

## The code

Read-only, and the same bytes the image runs. It is here because the founder asked whether we can show it: the page is the explanation, this is the appendix.

<ViewSource step="record_friction_fixed">

```python
@reg.step
def record_friction_fixed(ctx: StepCtx):
    """#1510's C3 — the same reasoning as `record_friction`, one door over: `POST
    /friction/{id}/fix` calls `admit()` directly, and this step exists only so THAT admission
    creates a reaction row `flows_timeline.friction_for_subject` can fold into its `status`
    field. No mail, no desk card, no downstream effect."""
    return Done({"recorded": True})
```

</ViewSource>
