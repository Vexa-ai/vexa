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
  - **Non-terminal value-fsm ⇒ pending, not failure.** The `labeled` event that fires the card
    also re-triggers `value-fsm`, so its newest run on head is frequently still `queued`/
    `in_progress` (`conclusion === null`) when the card evaluates. The card treats that as
    **pending** and **waits** for a terminal verdict (bounded poll) rather than reading an
    in-flight run as failure — the #655 race, which red-carded correctly-approved PRs and forced a
    manual rerun. A value-fsm that never reaches terminal `success` within the wait budget stays
    not-mergeable (the label cannot waive it; success must be positively observed).
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

## Amendment 2026-10-08 — architecture and security passes

**Decision.** The card gains two rows that gate **every** PR, docs and CI included: **Architecture**
and **Security**. Each clears only when a PR comment from an account with write or admin on the
repo (the maintainer check the Diff row already uses) carries a marker for the PR's current head
sha:

```
<!-- vexa-pass:architecture sha=<full head sha> verdict=pass -->
<!-- vexa-pass:security sha=<full head sha> verdict=pass -->
```

- **Markers lead the comment.** A marker counts only in the comment's leading lines: from the
  first line, each starting at column 0 with `<!--` and closing `-->` on the same line, with blank
  lines allowed between them. GitHub always hides those lines, and nothing above them can open
  code. Nothing after the first other line counts, so the card never has to read Markdown. A
  comment that leads with its marker may quote other markers below it. A maintainer's marker for
  the head that stands only lower in a comment fails the row, if it is the newest marker for the
  head, and the row says it must be the first line of the comment: placement can take a pass
  away, never grant one.
- `verdict=waived` clears a row only when the same marker carries `waived-by=<login>`, naming an
  account with write or admin on the repo. The card shows the waiver as recorded by the commenter
  and names that account.
- A marker for any other sha does not count. The row names the sha the pass on record was for and
  asks for a re-run. Among maintainer markers for the head, the newest wins. The card reads the
  newest 1,000 comments; on a longer thread an older pass does not count and must be re-posted.
- **The head is the commit the check is posted for.** A `pull_request` or `pull_request_review`
  run judges its event's head and fails without judging if the PR has moved on. A `merge_group`
  run judges the PR's head as queued; a push takes the PR out of the queue.
- **Security findings stay private.** The marker and its comment carry only the verdict, an
  optional `findings=<n>` count and the sha. Findings follow `SECURITY.md` (coordinated disclosure)
  and never appear on the PR.
- **How a pass takes effect.** A comment does not trigger `merge-card`. When a repo-associated
  account posts, edits or deletes a comment carrying a marker, `merge-card-pass.yml` re-runs the
  PR's newest `merge-card` run on the head, and its `merge-card-comment` run so the sticky card
  agrees. It finds the run by PR as well as head sha (each card run is named after its PR, since
  two PRs can share a head), takes the sticky card's `pull_request_target` run, never re-runs a run
  awaiting approval, re-reads the PR head just before each re-run and skips it if the head moved
  or cannot be read, and decides nothing itself. A pass names one sha, so after a move it must be
  re-posted for the new head. The run name is read from the copy of the workflow that ran, so on a
  base branch without this change the pick falls back to the run's `pull_requests` list.
  - *Alternative considered:* the comment-triggered workflow publishes a `merge-card` check run on
    the head itself, as `contribution-rights.yml` does for its own check from an `issue_comment`
    run. Not taken: the `merge-card` check would then have two writers. With a re-run,
    `merge-card.yml` stays its only writer (P23).
  - *Costs of the re-run:* it needs an existing run for the PR on the head (none: push or re-run
    by hand); it waits out a run in flight, so it can hold a runner for up to about 25 minutes;
    and GitHub re-runs a run only within 30 days of it.

**Why.** Founder ruling 2026-10-08: the architecture and security checks are obligatory at PR
level. Until now they were review practice (D-S, TAKE step 3), so a PR could merge without either.

**Consequences.**

- Every push resets both rows: a pass is evidence about one tree, not about a branch.
- The card's script runs from `main` for PRs into every base branch, so release-line PRs render the
  rows. Whether a red card blocks the merge is the base branch's protection: today only `main`
  requires `merge-card`.

## Amendment 2026-10-28 — the reviewer agent signs a small radius

**Decision.** The two pass rows keep their markers, placement and head binding; what changes is
whose marker counts.

- **Radius.** `scripts/pr-radius.mjs` classifies a PR from its diff paths (a rename brings its old
  path). **Large** when any path is of a large kind: database (schema, models, SQL, migrations),
  contracts (contract dirs, goldens, JSON schemas, seals), bot protocol, seam, billing, auth and
  identity, gateway code, the merge gates and scanners themselves, deploy, release, shared
  libraries and repo-wide manifests; or when runtime code spans two or more units; or when a path
  is unclassified, or the file list is incomplete. **Small** otherwise: CI, docs and tests in any
  number, plus code in at most one unit. The card shows it as a Radius row that gates nothing.
- **The reviewer.** `vexa-reviewer.yml` runs on `pull_request_target`, so the default branch's copy
  runs and a PR cannot change what reviews it. It checks out `main` only, reads the PR from the API
  as text (title, body, files, the base...head diff), and runs Claude Code with every tool disabled,
  no MCP servers, in an empty directory: it never runs the PR's code or sees its author's session.
  It posts `vexa-pass` markers as the GitHub App `vexa-reviewer[bot]`, which holds issues and
  pull-requests write, no push, and is not a maintainer. The reply is parsed strictly; a pass that
  names a blocking finding is a fail. Security findings are age-encrypted to the maintainer and
  kept as a run artifact; the comment carries their count.
- **The card.** A reviewer marker for the head counts only on a small radius and only while every
  scanner on the head agrees (newest run per name: success, neutral or skipped). A red scanner is a
  disagreement and the row goes to a maintainer; a running scanner is waited out like value-fsm.
  A reviewer `verdict=fail` counts on any radius and blocks until a newer maintainer pass. The
  reviewer cannot waive. No marker means a maintainer signs: there is no fallback path.

**Why.** Founder ruling 2026-10-28: the founder signed architecture and security passes by hand on
every PR of 0.13.4, a 3-file CI fix included, and an agent posting them under the founder's account
is rightly blocked as self-approval. Small radius (CI, docs, tests, one service without database or
contracts) is signed by an independent reviewer that did not see the author's work; large radius
(database, contracts, bot protocol, billing, auth) stays the founder's.

**Consequences.**

- The founder sees large-radius PRs, reviewer fails and reviewer–scanner disagreements; a small
  PR the reviewer and the scanners agree on merges on the value sign-off and the diff row alone.
- The reviewer reads untrusted text. A PR can try to steer it; the prompt fences the PR as data and
  treats steering as a blocking finding, and the radius bounds what a fooled reviewer can sign.
- Changing `pr-radius.mjs`, the card or the reviewer is itself a large-radius change.
- Like the rest of the card, the rule is live where the base branch requires `merge-card`, and
  takes effect from `main` (the workflows check out `main`'s scripts).

