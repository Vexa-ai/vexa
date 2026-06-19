# AGENTS.md — the operating contract (read before you act)

This repo is governed by **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** — the constitution (P1–P21,
the gate suite, the development process). This file is the *front door*: how you run a session in it.

## How you work — the expectation–reality loop (§8)
1. **Expect first.** State what the system *should* do and what "done" looks like for the current
   objective *before* acting. You can't detect a divergence you never defined.
2. **Instrument by default — definite validation.** Prove with deterministic gates / tests / the `eval/`
   `replay`·`analyze`·`benchmark` path. No human where an instrument can decide. "It ran" is a claim; the
   instrument is the proof.
3. **The human is the scarcest resource — and fallible.** Spend human validation **last and least**. When
   only a human can decide: **minimise** the ask, give a **minimal, fully-instructed surface** (the exact
   `🧑` step), and **cross-validate it — never take "it works" as definitive** (it is *intent*, not
   evidence — P21). Confirm with an instrument first (ping the service, census the tape).
4. **An unexpected error is a STOP.** Reality ≠ expectation ⇒ stop. No paper-over, no blind retry.
5. **Root-cause every surprise to an architectural gap, and codify it.** Each surprise is a *symptom* of a
   missing/violated principle — fix the instance, then close the gap as a principle + its gate. This is
   how the principle-set grows (P18→P21 were each born this way). See ADR-0014.

> *Expect → instrument → (human: minimal, cross-validated) → stop on surprise → root-cause to a principle → codify.*

## The hard rules (from the constitution)
- **Green or it didn't happen.** `pnpm gates` must pass; an artifact "exists" only when gate-green (P9).
- **Prove at the altitude of the claim (P19).** A user-facing behaviour needs **L4** evidence, not just
  L1–L3 green. Name which level a "green" rests on.
- **Report state from evidence, not intent (P21).** Don't claim a success you haven't observed.
- **Contracts & principles ride `lane:contract`** — a human-reviewed change, recorded as an ADR under
  `docs/adr/`. Everything else merges on green gates.
- **Fix in the brick that owns the symptom; reproduce with no live meeting before you fix.**
