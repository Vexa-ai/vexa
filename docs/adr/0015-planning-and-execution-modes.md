# ADR 0015 — Planning mode & execution mode; the always-current release plan

**Status:** accepted · 2026-06-19 · governs §8 (development process); composes ADR-0014

## Context

The expectation–reality loop (ADR-0014) governs a *single step*: expect, instrument, stop on surprise,
root-cause. But a step's expectation only makes sense inside a **larger path** — where we are, where the
release is, and the staged route between them. Without a maintained plan, execution drifts into
improvisation: stages get skipped, work is marked "done" against no agreed definition, and a surprise has
no plan to loop back to. The macro expectation must be as explicit as the micro one.

## Decision

Adopt **two interlocking modes** and a **standing plan**:

- **A current plan always exists** — the *full path* from the present state to the current dev-release
  objectives: staged, each stage ending at a runnable proof, with the critical path and the parallel
  workstreams marked. It is a **maintained, versioned doc** in the repo
  (`docs/RELEASE-PLAN.md`), not held in memory or an ephemeral artifact.
- **Planning mode** produces or revises that plan *before* building — decompose the objective, mark
  parallelism, set each stage's definition-of-done. Its output is an approved plan.
- **Execution mode** runs the plan one stage at a time under the expectation–reality loop (ADR-0014):
  instrument by default, the human minimal + cross-validated, stop on surprise.
- **The modes interlock.** A surprise that root-causes to a principle gap, or a changed objective, returns
  to **planning mode** — the plan is revised, then execution resumes. The plan is living and always
  current; one is always in exactly one mode, never improvising without a path.

## Consequences

- `docs/RELEASE-PLAN.md` is kept current as the single macro expectation; "what's done / in-flight /
  remaining" and the current objective are always answerable from it.
- The plan and the loop compose: the plan sets the staged expectations, the loop enforces each one and
  feeds gaps back. Re-planning is a first-class step, not a failure.
- Cost: maintaining the plan doc as state changes. Cheap relative to the cost of execution drifting off an
  un-stated path.
