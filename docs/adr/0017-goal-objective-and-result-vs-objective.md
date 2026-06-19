# ADR 0017 — Goal vs objective; the objective lifecycle; a report assesses result vs objective

**Status:** accepted · 2026-06-20 · composes ADR-0014/0015/0016 (§8)

## Context

"Objective" was used loosely, and reports stated *facts* but not the objective those facts were being
assessed against — so a result had no reference frame ("789 lines, tests green" → green *against what?*).
And the unit of work between "the plan" and "a single step" was unnamed.

## Decision

**Vocabulary.**
- **Goal** = the destination — the *end of the plan* (the 0.12 release).
- **Objective** = the *current go* — the one waypoint being executed toward right now. The plan is the
  ordered path of objectives from here to the goal.

**The objective is the unit of execution and assessment.**
- You are always executing toward exactly **one open objective** (execution mode) — you never drift
  between objectives.
- **A report assesses the actual result-state against the current objective** — *result vs objective*.
  So a report **names the objective first**, then the raw facts (ADR-0016), so result-vs-objective is
  assessable rather than asserted.

**Objective closure — two ways:**
- **Expected** — the result met the objective. Close it and take the **next objective**, still in
  execution mode, autonomously.
- **Unexpected** — the result diverged. **Stop and interpret it *with the human*, as learning**:
  root-cause to the architectural gap (the expectation–reality loop, ADR-0014), codify it, then re-plan
  and resume. Expected closure flows autonomously; **unexpected closure pulls in the human** (the
  ground-truth interpreter) and grows the principle-set.

## Consequences

- Every report is assessable: it carries `objective + result (facts) → expected | unexpected`.
- Expected closures chain without ceremony; unexpected ones are the learning events that produce
  principles (P18→P21, ADR-0014/16 were each an unexpected closure interpreted with the human).
- `docs/RELEASE-PLAN.md` states the **goal** (release) and the **current objective**; one is always in
  exactly one open objective.
