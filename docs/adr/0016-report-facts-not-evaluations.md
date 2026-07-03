# ADR 0016 — Report facts, not evaluations

**Status:** accepted · 2026-06-20 · composes P21, ADR-0014 (§8)

## Context

Status was being reported as **evaluations** — "P2-2a done", "L3-validated", "gates green" — which are
*interpretations*. They ask the human to inherit a conclusion instead of reaching their own. That is
backwards: the human is the ground-truth interpreter (ADR-0014), and an interpretation can be wrong (the
`capture` tool *passed* while mis-calling a healthy gmeet "unhealthy"). An evaluation without its evidence
is an unbacked claim — exactly what P21 forbids, applied to communication.

Concretely, "2a — L3-validated" hid the one fact that changes its meaning: the adapter tests inject
**fake** clients and **never construct the real node-redis client or contact a broker** — so the
contract-shaping logic is proven, but the real redis I/O is type-checked only. The human can't interpret
"validated" correctly without that fact.

## Decision

**Every reported result ships with the raw evidence that produced it.** When stating an outcome, include:

- the **command(s) run and their actual output** (or the precise observation), not a paraphrase;
- the **counts and names** of what was checked (which tests, which gates), with pass/fail;
- crucially, **what was NOT covered** — fakes vs real, type-checked vs executed, instrument vs human,
  the boundary of the claim;
- the **interpretation is stated separately and labelled as such** ("my reading: …"), downstream of the
  facts — so the human reaches their own conclusion and can overturn mine.

An evaluation ("done", "validated", "works", "passing") never travels alone. *Surface the facts; let the
ground-truth interpreter interpret.*

## Consequences

- Reports get longer and more useful: the human can catch a wrong instrument or an over-broad claim,
  because the evidence is in front of them, not summarised away.
- Composes P21 (state from evidence) and ADR-0014 (instruments are cheap-but-approximate; the human
  interprets) — this is the *communication* face of the same rule.
- Recorded in `ARCHITECTURE.md` §8 and the root `AGENTS.md`.
