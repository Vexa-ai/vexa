---
kind: flow
flow: friction_log
version: 1
trigger: friction.reported
steps: 1
generated: from the code that runs it — edits here are overwritten
---

# friction_log

Runs when **`friction.reported`** happens, in 1 step. This page is written from the code — the docstrings below are the ones in the image that is running, and the Python at the foot is that code verbatim.

| | |
|---|---|
| **trigger** | `friction.reported` |
| **version** | 1 — a step list changes by adding a version, never by editing one in place |
| **mails** | nothing |
| **rules it honours** | none |

## The steps, in order

### 1. `record_friction`

THE WHOLE OF THE FLOW SIDE OF PRD 40.9 open-decision 8. `POST /friction` already wrote the fact by calling `admit()` directly (`flows_integrations/flows_api.py`) — this step exists ONLY so that admission has a matching flow to create a REACTION ROW for. `admit()` creates one reaction per matching flow and zero for none (`flows/admission.py`): without a registered flow here, `friction.reported` would be admitted into nothing, and `flows_timeline` — which reads only `reaction`/`effect_receipt`, never a raw event log — would never show a single report. It does nothing else: no mail, no desk card, no downstream effect, and it finishes on its first tick.

- **domains:** reaches no other domain

## The code

Read-only, and the same bytes the image runs. It is here because the founder asked whether we can show it: the page is the explanation, this is the appendix.

<details>
<summary>view source — <code>record_friction</code></summary>

```python
@reg.step
def record_friction(ctx: StepCtx):
    """THE WHOLE OF THE FLOW SIDE OF PRD 40.9 open-decision 8. `POST /friction` already wrote
    the fact by calling `admit()` directly (`flows_integrations/flows_api.py`) — this step
    exists ONLY so that admission has a matching flow to create a REACTION ROW for. `admit()`
    creates one reaction per matching flow and zero for none (`flows/admission.py`): without a
    registered flow here, `friction.reported` would be admitted into nothing, and
    `flows_timeline` — which reads only `reaction`/`effect_receipt`, never a raw event log —
    would never show a single report. It does nothing else: no mail, no desk card, no
    downstream effect, and it finishes on its first tick.

    Reaches no domain: `needs` is empty on purpose, so this runs — and the sink stays a sink —
    in every profile, agent or not."""
    return Done({"recorded": True})
```

</details>
