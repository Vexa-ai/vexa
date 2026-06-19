# AGENTS.md — the operating contract (read before you act)

This repo is governed by **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** — the constitution (P1–P21,
the gate suite, the development process). This file is the *front door*: how you run a session in it.

## Plan first — two modes
Never build without a current plan. **Planning mode** produces/revises the *full path* to the dev-release
objectives ([`docs/RELEASE-PLAN.md`](docs/RELEASE-PLAN.md) — the always-current, staged plan); **execution
mode** runs it one stage at a time under the loop below. A surprise or changed objective → back to
planning. You are always in exactly one mode (ADR-0015).

## How you work — the expectation–reality loop (§8)
1. **Expect first.** State what the system *should* do and what "done" looks like for the current
   objective *before* acting. You can't detect a divergence you never defined.
2. **Validate cheaply by instrument — but it's approximate.** Gates / tests / the `eval/`
   `replay`·`analyze`·`benchmark` path do the broad, cheap filtering — fast, reproducible, no human. But
   pass/fail is a *proxy*: it can mis-define success or mis-interpret the signal. Cheap, not definitive —
   green is necessary, never sufficient (P19).
3. **The human is the ground-truth oracle — scarce + fallible.** Only a human can finally tell if it
   *actually works* — use the human where cheap tests can't define/interpret success. Spend it **last and
   least**, give a **minimal, fully-instructed surface** (the exact `🧑` step), and **cross-validate both
   ways** — a green instrument is provisional; a human "it works" is checked against an instrument. Then
   **instrumentalise the human's verdict** (a golden / eval baseline) so the cheap test calibrates to it.
4. **An unexpected error is a STOP.** Reality ≠ expectation ⇒ stop. No paper-over, no blind retry.
5. **Root-cause every surprise to an architectural gap, and codify it.** Each surprise is a *symptom* of a
   missing/violated principle — fix the instance, then close the gap as a principle + its gate. This is
   how the principle-set grows (P18→P21 were each born this way). See ADR-0014.

6. **Report facts, not evaluations.** Every result ships with its **raw evidence** — the command + actual
   output, the counts/names of what was checked, and **what was NOT** (fakes vs real, type-checked vs
   executed, instrument vs human). "Done"/"validated"/"works" is *your* interpretation — state it
   separately and labelled, downstream of the facts, so the human reaches their own (P21, ADR-0016).

> *Expect → instrument → (human: minimal, cross-validated) → stop on surprise → root-cause to a principle → codify. Report facts, not evaluations.*

## The hard rules (from the constitution)
- **Green or it didn't happen.** `pnpm gates` must pass; an artifact "exists" only when gate-green (P9).
- **Prove at the altitude of the claim (P19).** A user-facing behaviour needs **L4** evidence, not just
  L1–L3 green. Name which level a "green" rests on.
- **Report state from evidence, not intent (P21).** Don't claim a success you haven't observed.
- **Contracts & principles ride `lane:contract`** — a human-reviewed change, recorded as an ADR under
  `docs/adr/`. Everything else merges on green gates.
- **Fix in the brick that owns the symptom; reproduce with no live meeting before you fix.**
