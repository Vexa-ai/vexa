# ADR 0021 — Planning embeds the governing rules inline; the plan is self-bounding

**Status:** proposed · 2026-06-21 · `lane:contract` · enforces §8 "planning ⇄ execution" + P9/P19/P21

## Context

A plan that merely **references** the constitution (P-numbers, ADR links) lets execution drift: the rule is a
lookup away, so a hop can quietly violate a principle no one re-read. The constitution already says "a rule in a
README rots; a rule that turns CI red cannot be crossed" (P9) and "you can't detect a divergence you never
defined" (§8.1). Planning is where the expectation is set — so the rules and the falsifiable end-state must be
*in the plan*, not behind a link.

## Decision

**A plan embeds the governing rules inline and walks every objective as a visible hop bound to a defined
end-goal.**

- The plan carries a **"Rules in force"** block: the *text* (not just the tag) of every principle/loop-rule the
  work must obey, so execution is bound to the constitution without a lookup.
- The plan states the **end-goal** as a **falsifiable, gate-backed** definition-of-done (ADR-0017 *goal vs
  objective*) — the macro-expectation every hop walks toward.
- Each objective is a **hop**: *Objective → Expected (falsifiable, before acting) → Observation (raw evidence +
  what was NOT checked) → Verdict (EXPECTED → continue · UNEXPECTED → STOP, root-cause to a principle, promote
  the learning twice, re-plan) → re-check against the end-goal.* Each hop names the specific principles/gates it
  must satisfy, and an explicit **"Unexpected if:"** trigger.
- A "better than X" claim is operationalized as a **specific green gate** (P9), never prose.
- A plan without inline rules + per-hop `Expected` is **not in the loop** and is not approved. `AGENTS.md` gains
  §"Planning embeds the rules".

## Consequences

- Plan→execution drift is structurally resisted: the rule, the expectation, and the unexpected-trigger are on
  the page, so a divergence registers as a STOP instead of silently passing.
- Reporting stays honest (ADR-0016/P21): each hop ships facts first, the "done" interpretation second.
- Cost: longer plans. Accepted — the plan is the macro-expectation; under-specifying it is what lets work drift.
- The validation plan that introduced this ADR is its reference implementation.
