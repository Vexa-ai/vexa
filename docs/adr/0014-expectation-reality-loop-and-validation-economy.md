# ADR 0014 — The expectation–reality loop & validation economy (how we run)

**Status:** accepted · 2026-06-19 · governs §8 (development process); composes P18/P19/P21

## Context

This milestone produced four principles (P18–P21) the same way each time: a **surprise** —
behaviour that diverged from what we expected — and a refusal to paper over it, followed by tracing it
to the architectural gap that allowed it. The pattern was implicit. It should be the *explicit* operating
contract, because it is what keeps the principle-set honest and growing.

Two forces, both learned the hard way this session:

- **Validation source matters.** The reliable findings came from **instruments** — deterministic gates,
  the `eval/` `replay`/`analyze`/`benchmark` path, a direct STT ping, a tape census. The misleading
  signals came from **un-cross-validated human reports**: "I updated the balance" (the service still
  returned `402` — wrong account), "gmeet doesn't transcribe" (the tape showed a solo meeting, not a
  bug). A human assertion is *intent*, not evidence (P21) — yet the human is also the **highest-level and
  scarcest** resource, and **fallible**.

- **Surprises are signals.** "Listening over 0 streams", a silent `402`, gate-green-but-broken — each was
  a symptom of a missing principle, not a one-off to retry past.

## Decision

Adopt the **expectation–reality loop** as §8's governing discipline:

1. **Expect first** — state the expected behaviour / definition of done for the current objective before
   acting; you can't detect a divergence you never defined.
2. **Instrument by default (definite validation)** — prove with deterministic gates/tests/eval, no human
   in the loop where an instrument can decide.
3. **Human validation is scarce, fallible, cross-validated** — spend it last and least; when required,
   give a **minimal, fully-instructed surface** (the exact `🧑` step); and **cross-validate it with an
   instrument** — never treat a human "it works" as definitive.
4. **An unexpected error is a STOP** — reality ≠ expectation halts the work; no paper-over, no blind retry.
5. **Root-cause to a principle, and codify** — trace the surprise to the missing/violated principle, fix
   the instance, and close the gap as a principle + its gate (the P18→P21 mechanism).

This is recorded in `ARCHITECTURE.md` §8 and fronted by a root `AGENTS.md` operating contract.

## Consequences

- The principle-set has an explicit **growth mechanism**: every genuine surprise either matches an
  existing principle (apply it) or exposes a gap (codify it). The constitution evolves by evidence, not
  by taste.
- Human time is treated as the **bottleneck resource** it is: instruments do the definite validation;
  humans are asked rarely, precisely, and their input is verified — reducing both human load and the
  blast radius of human error.
- Cost: stating expectations up front and stopping on surprises is slower per step than pushing through —
  and far cheaper than the hours a silent divergence costs (the `402` that read as "the extension is
  broken").
