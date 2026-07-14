# ADR 0030 — The merge card is enforced: value AND diff must both be accepted before merge

**Status:** accepted · 2026-07-14 · enforces **D1** (a rule that isn't enforced is a comment),
**D8** (bundle + diff), **D9** (value-signed), **D10** (acceptance floor) and the **merge bar** /
choke point 1 · sibling of [ADR-0029](0029-release-witness-and-value-gates-enforced.md) (choke
point 2, the release witness)

## Context

The delivery constitution's merge bar is explicit: *"A PR carries two artifacts judged on
different axes: the observation bundle (is the value real?) and the diff (is it correct and
safe?)"*, and main *"is always releasable, never auto-released"* — a PR enters it only when **the
value is signed** and **the diff passes review**. Choke point 1 is *the merge card, one per PR*.

But the machinery enforced neither. `main`'s branch protection required exactly one status check —
`gates` (L1/L2 soundness) — and nothing else. So a PR could (and routinely did, e.g. the entire
v0.12.4 reliability batch) merge on CI-green alone: no value sign-off, no diff review. The value
and the diff being *accepted* lived only in prose. Per D1, that made the merge bar a comment.

## Decision

**A required `merge-card` status check gates every merge to `main`. It passes only when BOTH the
value and the diff are accepted:**

- **Value accepted** — for a runtime PR (pr-value's path filter), `value-fsm` is GREEN on the head
  sha **and** the PR carries `state: value-signed` (the D9 sign-off). A red value-fsm is never
  waived by the label. Non-runtime PRs (no pr-value leg): `state: value-signed` alone.
- **Diff accepted** — a GitHub review **approval from a non-author** whose `commit_id` is the
  current head sha (a new push dismisses a stale approval — re-review required).

`scripts/merge-card-gate.mjs` implements the card. `.github/workflows/merge-card.yml` runs it on
`pull_request` + `pull_request_review` (PR entry) and on `merge_group` (the queue re-check, where
the PR number is parsed from the queue ref, so the check is green on the exact tree that merges).
The check is added to `main`'s branch protection contexts alongside `gates`, and `enforce_admins`
is enabled so the card binds admins too (no silent bypass).

**Contributor onboarding (recommended, never a gate).** The PR template and CONTRIBUTING invite
authors to explain their change on Discord, and `pr-welcome.yml` posts a one-time friendly nudge on
a first-time contributor's PR. This is hospitality — it gates nothing; a PR is judged on its
evidence, not on whether the author shows up.

## Consequences

- **Choke point 1 is now machinery.** No PR — including a maintainer's or an agent's — merges to
  main without a value sign-off and a non-author diff approval. The autonomous merge-on-CI path is
  closed; the human moment the constitution designed is enforced.
- **The two choke points now match** (ADR-0029 = release witness; ADR-0030 = merge card): both are
  required checks, both bind admins, both fail with a plain-language statement of what's missing.
- **Cost:** every PR now needs an explicit value sign-off (`state: value-signed`) and a non-author
  approval before it can be queued. That is the intended cost of D0 (verified truth about value).
- **Ordering:** the `merge-card` workflow must be on `main` before the check is added to branch
  protection (else every PR blocks on a check that never runs). This ADR's PR merges under the old
  bar (`gates` only); the branch-protection change is the immediately-following step.
- **Not covered** (honest TO BUILD): channel-corroboration checking (the attestation's
  human/instrument consistency, D9) and label-actor authorization (who may apply `value-signed`)
  remain manual/maintainer-practice.

## Enforcement map

`docs/docs/governance/delivery.mdx`: new rows for *Merge bar — value + diff accepted* (**have**,
CI) and *Contributor onboarding* (**have**, templates + CI).
