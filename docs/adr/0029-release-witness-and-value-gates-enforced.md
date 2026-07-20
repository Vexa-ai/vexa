# ADR 0029 — The release witness and batch-value acceptance are enforced by the machinery

**Status:** accepted · 2026-07-14 · enforces **D0** (verified truth about value), **D1**
(a rule that isn't enforced is a comment), **D9/D10/D15** and the ship-bar guarantee lines **7**
and **8** · flips those enforcement-map rows from **TO BUILD** to **have (CI)**

## Context

The delivery constitution's guarantee is owner-approved canon (2026-07-13):

- **Line 7** — *"A human witnessed the assembled value… **No signature, no release.**"*
- **Line 8** — *"Every change in the batch was individually proven before it entered."*
- Ship bar — *"Publish and promote are the same act for witness purposes: **neither happens
  before the witness pass is signed.** A release published unwitnessed is retracted to
  pre-release until the pass is met."*

But the **machinery did not enforce any of it.** `release-images` called `release-validate` with
`promote: true` on the tag-push path, so `:v012` moved automatically the moment the L4 legs went
green — no witness, no batch-acceptance check. The GitHub Release was hand-created with prose
*claiming* the witness. This is exactly the v0.12.1/v0.12.2 failure the constitution names: a
machinery-green release promoted and published as if value-witnessed, with the witness still
unsigned. Per **D1**, an unenforced rule is a comment — lines 7 and 8 were comments.

The enforcement map listed the fix as three **TO BUILD** rows (D9 value-gate, D10 bundle checker,
D15 release-set gate). This ADR builds the release-level slice of them.

## Decision

**Publish and promote become separate acts, and promote is gated on witness + value acceptance.**

1. **Split publish from promote.** `release-images` now hands off to `release-validate` with
   `promote: false`. A tag push builds + publishes the versioned `:vX.Y.Z` images and proves them
   (L4) — the candidate *exists to be witnessed* — but never moves `:v012`.

2. **`witness-gate` (guarantee 7).** A `release-validate` job (runs only on a promote request)
   that requires `releases/<version>/witness.json`: present, well-formed, version-matched, with
   the meeting/transcript/live-stream evidence, the batch values walked, and `signed_off: true`.
   The receipt is the **evidence**; the **hard human gate** is a new GitHub Environment
   **`release-promote`** with the owner as required reviewer — a CI file cannot forge an approval,
   so both are required. `promote` hard-`needs` this job and runs `environment: release-promote`.

3. **`value-gate` (guarantee 8).** A `release-validate` job that enumerates the batch (PRs merged
   between the previous release tag and this one) and requires each to be accepted: `value-fsm`
   (the pr-value L3 leg) green on its head sha, or `state: value-signed`, or — for a PR touching
   no runtime surface — merged through the full `gates` suite. `promote` hard-`needs` it.

4. **`release-published-guard`.** On `release: published`, re-checks both gates against `main`
   and, per the ship bar, **flips an unwitnessed/unaccepted release back to draft**. This closes
   the hand-created-release bypass.

The scripts (`scripts/release-value-gate.mjs`, `scripts/release-witness-gate.mjs`,
`scripts/release-witness-template.mjs`) are dependency-free ESM, locally runnable
(`pnpm gate:release-value` / `gate:release-witness` / `witness:template`), and were verified
against the real v0.12.3 batch (5/5 accepted) and the v0.12.4 batch (all heads `value-fsm`-green).

## Consequences

- **A release cannot promote or stay published without a signed witness pass and an accepted
  batch.** Choke point 2 of the constitution is now machinery, not discipline. The v0.12.1 class
  of incident (promote on L4 alone) is structurally impossible.
- **The witness receipt is auditable** — every release carries `releases/<version>/witness.json`
  under version control, naming who witnessed what, on which deployment, with links.
- **The verdict-latency law is preserved** — build+publish and validate+promote stay separate and
  independently dispatchable; a witness/receipt fix re-runs only the second half.
- **Cost:** the release now has an explicit human step (owner approval on `release-promote`) and a
  committed receipt before `:v012` moves. That is the intended cost — the scarce input is verified
  truth about value (D0), and this is where we spend it.
- **Not covered here** (honest TO BUILD, tracked on the roadmap): machine-generating the release
  notes/guarantee block from the ledger (guarantee line 9), and the per-PR merge-card gate (D9's
  merge-time half). This ADR is the release-time slice.

## How this changes the constitution

`docs/docs/governance/delivery.mdx` enforcement map: rows D9/D10/D15 move from **TO BUILD** to
**have (CI)** for the release-time gates, with a pointer to this ADR.
