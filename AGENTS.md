# AGENTS.md — the operating contract (read before you act)

This repo is governed by **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** — the constitution (P1–P21,
the gate suite, the development process). This file is the *front door*: how you run a session in it.

## One worktree per chat
Concurrent work is isolated: each chat works in its **own `git worktree`** on a short-lived branch
(`git worktree add ../v0.12-<slug> -b chat/<slug>`), integrating to the integration branch via **PR**
(gates required; `lane:contract` human-gated). **Never two chats on one working tree**, and **never touch
another chat's uncommitted files** — if surprise files appear from another chat on a shared tree, surface
them, don't adopt them (ADR-0019).

## Plan first — two modes; goal vs objective
Never build without a current plan. **Planning mode** produces/revises the *full path* to the **goal**
([`docs/RELEASE-PLAN.md`](docs/RELEASE-PLAN.md) — the always-current, staged plan); **execution mode** runs
it one **objective** at a time under the loop below. **Goal** = destination (end of plan = the release);
**objective** = the current *go*. You are always executing toward exactly one open objective; it closes
**expected** (→ next objective, autonomously) or **unexpected** (→ stop, interpret *with the human*, as
learning → codify, re-plan). Never drift; always in exactly one mode (ADR-0015/0017).

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
   **But UNEXPECTED ≠ unfinished work.** A scaffold you're meant to complete, a stub awaiting its impl, "not
   built yet" — that is *expected work*: **finish it autonomously, do not stop or escalate.** STOP is reserved for a
   genuine *contradiction* (a frozen contract field that can't be met, a false premise, a real conflict) — not for
   remaining scaffolding (Learning #21).
5. **Root-cause every surprise — earn the learning with the human, promote it twice.** Each surprise is a
   *symptom* of a missing/violated principle. Interpret it **with the human** (never mint a learning from
   an instrument alone), fix the instance, then promote it to **both** the **architecture** (a principle +
   gate + ADR) **and** the **learnings log** ([`docs/LEARNINGS.md`](docs/LEARNINGS.md) — always, even for a
   practice/candidate with no P-number). How the principle-set grows (P18→P21). See ADR-0014/0018.

6. **State the objective, then report facts — not evaluations.** A report assesses the result-state
   against the **current objective** (*result vs objective*), so name the objective first, then ship the
   **raw evidence** — the command + actual output, counts/names of what was checked, and **what was NOT**
   (fakes vs real, type-checked vs executed, instrument vs human). "Done"/"validated"/"works" is *your*
   interpretation — state it separately and labelled, downstream of the facts, so the human assesses
   result-vs-objective themselves (P21, ADR-0016/0017).

> *Expect → instrument → (human: minimal, cross-validated) → stop on surprise → root-cause to a principle → codify. Report facts, not evaluations.*

## Every hop is VISIBLE — the objective ledger (the forcing function)
The loop above silently degrades into "do → report" unless each hop is **written before acting**.
With no stated expectation there is nothing for reality to violate, so nothing ever registers as
*unexpected* — and the learning mechanism never fires. So **every** objective is stamped in this exact
shape, hop by hop, where the human can see it:

- **Objective:** the one current *go*.
- **Expected:** the concrete, **falsifiable** result predicted *before* acting — the specific
  numbers / states / shapes that "done" will show, and how they'll be checked.
- *…act…*
- **Actual:** raw evidence — command + output, counts/names, and **what was NOT** checked.
- **Verdict:** **EXPECTED** → continue to the next planned objective (state the next one + its Expected) ·
  **UNEXPECTED** → **STOP**, interpret *with the human*, learn (loop step 5), re-plan.

**No `Expected` written ⇒ you are not in the loop.** An objective that closes without an explicit
Actual-vs-Expected **Verdict** is incomplete. There is no automated check for this — **the human reading
the ledger IS the gate** (the visibility is the enforcement). Keep `Expected` short and testable; an
unfalsifiable expectation ("it should work") can't catch a surprise.

## The hard rules (from the constitution)
- **Green or it didn't happen.** `pnpm gates` must pass; an artifact "exists" only when gate-green (P9).
- **Prove at the altitude of the claim (P19).** A user-facing behaviour needs **L4** evidence, not just
  L1–L3 green. Name which level a "green" rests on.
- **Report state from evidence, not intent (P21).** Don't claim a success you haven't observed.
- **Contracts & principles ride `lane:contract`** — a human-reviewed change, recorded as an ADR under
  `docs/adr/`. Everything else merges on green gates.
- **Fix in the brick that owns the symptom; reproduce with no live meeting before you fix.**
