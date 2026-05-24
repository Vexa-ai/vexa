---
name: develop
pipeline_index: 2
pipeline_name: 2-develop
description: Deliver one Vexa pack epic in isolation. Use when Codex needs to turn one accepted pack epic into a scoped branch/worktree, isolated Compose and Lite runtimes, synthetic-first validation, required live/human checks, Hardenloop evidence, and a PR targeting the release integration branch.
---

# 2. Develop

Pipeline index: `2`

Sequence: `pack` -> `develop` -> `release`

## Purpose

Deliver exactly one accepted pack epic as an isolated, reviewable update.

Input is one pack epic issue. Output is one PR plus evidence under:

```text
.agents/packs/<pack-id>/
```

This skill owns pack implementation. Do not implement pack code directly from
`pack` or `release`.

Develop may only take a GitHub pack epic that is labeled:

- `pack`
- `status:available`

Before creating a branch, worktree, runtime namespace, or code change, develop
must claim the pack by changing the GitHub labels from `status:available` to
`status:in-progress`. A local pack body can be used for dry/preflight planning,
but it cannot be implemented until the corresponding GitHub issue is available
and claimed.

## Required Inputs

Use one of:

- GitHub pack epic issue number or URL.
- Local pack epic body file for dry/preflight work.

The pack epic must follow the `pack` skill template and declare:

- pack id;
- release id;
- base branch;
- integration branch;
- runtime namespace;
- validation gates.

## Workflow

1. Parse the pack epic with `scripts/parse-pack-epic.py`.
2. Claim the GitHub pack epic with `scripts/claim-pack-epic.sh --apply`. This
   must reject issues without `pack` and `status:available`, remove
   `status:available`, and add `status:in-progress`.
3. Parse the pack epic again after claiming so evidence records the current
   lifecycle label.
4. Allocate non-default runtime ports/namespaces with
   `scripts/allocate-runtime.py`.
5. Run `scripts/pack-preflight.sh` before creating worktrees or touching code.
6. Create the isolated branch/worktree with
   `scripts/create-pack-worktree.sh --apply` only after preflight passes.
7. Implement only the pack scope in that worktree.
8. Run synthetic checks first. Do not ask for a real Google Meet or Microsoft
   Teams room when the behavior can be generated locally.
9. Validate local Compose through `compose-deploy` for every pack, then run
   pack-specific blast-radius checks against the Compose lane.
10. Validate local Lite through `vexa-lite-deploy` for every pack, then run
   pack-specific blast-radius checks against the Lite lane.
11. Obtain human eyeball validation before the pack can be PR-ready. Two
   distinct eyeball verdicts are required and BOTH are mandatory for every
   pack regardless of scope:
   - (a) **Basic functionality** — the human confirms overall user-facing
     behavior still works (sign-in, listing, opening a meeting, transcript
     surface) in both Compose and Lite.
   - (b) **Pack blast radius** — the human confirms the specific surfaces
     this pack touches (per the pack epic's blast-radius declaration) behave
     correctly in both Compose and Lite, including any UI/audio/playback
     state that only a human can sense.
   Record validator identity, timestamp, URLs/screenshots/log summaries
   reviewed, and the explicit verdict for each of (a) and (b) per lane.
   This gate is never waived by a pack epic saying live meetings are not
   required; external meeting validation is a separate boundary.
12. Run `vexa-meeting-deployment-test` for every pack against both the
   pack-specific Compose lane and the pack-specific Lite lane after synthetic,
   Compose, Lite, and human eyeball checks. Use the user-approved meeting
   URL(s), configure `https://httpbin.org/post` webhooks, and record separate
   Compose and Lite reports. A pack cannot be PR-ready until both lane reports
   have `Status: pass`.
13. Obtain a **human code review** verdict before the pack can be PR-ready.
   A human reviewer must read the actual code diff (not only the PR
   description) and record:
   - reviewer identity and timestamp;
   - explicit verdict (`pass`, `pass with notes`, `changes requested`,
     or `block`);
   - notes covering each declared blast-radius surface;
   - confirmation that the diff stays within the pack scope (no unrelated
     refactors, no hidden stitch-time changes).
   Record in `review.md` (machine-generated review skeleton allowed) and
   `code-review.md` (human-authored verdict). Both files are required.
   This gate is never granted by the develop skill or by Codex; it must
   come from a human signal in the chat or in the PR review thread.
14. Run `hardenloop` before the pack can be PR-ready.
15. Check evidence with `scripts/pack-evidence-check.py`.
16. Render the PR body with `scripts/render-pr-body.py`, open the PR, and keep
    it targeted at the release integration branch.

## Isolation Rules

- One pack, one branch, one worktree, one runtime namespace.
- One pack can be claimed by only one active develop run. The GitHub issue
  lifecycle label is the lock.
- Do not use default local ports such as `3000`, `8056`, or `8080` for pack
  lanes.
- Do not touch `tests3`. Helpful checks must live in product tests or skills.
- Do not make hidden stitch-time changes. If the pack needs a change, it belongs
  in the pack PR.
- Do not broaden the pack because an adjacent bug is convenient. If a bug is
  outside the pack and not a regression, file/log it and continue.
- Do not ask for human eyeball confirmation for machine-validated facts.
  However, every pack still requires a human eyeball verdict for overall
  basic functionality AND Compose/Lite blast-radius behavior before
  PR-ready status. These are two separate verdicts; one does not substitute
  for the other.
- Do not self-grant the code review verdict. Develop prepares the diff,
  the PR body, and a review skeleton (`review.md`); a human reads the diff
  and writes `code-review.md` with the explicit verdict. A pack is not
  PR-ready without a human code review verdict on file.

## Evidence Contract

Minimum PR-ready evidence:

```text
.agents/packs/<pack-id>/
  pack.json
  claim.json
  runtime.json
  ops/ops.jsonl
  tests/
  compose/
    meeting-deployment-test.md
    human-eyeball-basic.md
    human-eyeball-blast-radius.md
  lite/
    meeting-deployment-test.md
    human-eyeball-basic.md
    human-eyeball-blast-radius.md
  human/
    overall-functionality.md
  hardenloop/
  review.md
  code-review.md
  pr.md
```

Compose, Lite, both human eyeball verdicts (basic + blast-radius), live meeting
deployment, and human code review are mandatory for every pack. The live
meeting deployment gate must run against both Compose and Lite lanes with
separate evidence files. The two human eyeball files in each lane (`-basic`
and `-blast-radius`) capture the two distinct verdicts required by step 11;
combining them into one file is not allowed because the verdicts are
independent.

## Operation Ledger

Use the shared operation logger from the `release` skill. For pack work, write
to the pack ledger:

```text
.agents/packs/<pack-id>/ops/ops.jsonl
```

Pass `--log-file .agents/packs/<pack-id>/ops/ops.jsonl` and an `--out` path
inside the same pack ops directory when using `oplog-run.sh`.

Record every build, deploy, live meeting run, human wait, failed hypothesis, and
debug operation that matters to future wall-time optimization.

## Completion Response

When reporting progress, include:

- pack id and branch/worktree;
- synthetic validation status;
- Compose and Lite lane status, including blast-radius status;
- human eyeball verdicts — separately for basic functionality and for
  blast-radius, in both Compose and Lite (four verdicts total);
- external live-meeting validation status for Compose and Lite;
- Hardenloop status;
- human code review verdict (reviewer, timestamp, verdict, notes);
- PR/evidence readiness.
