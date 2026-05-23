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
9. Validate local Compose through `compose-deploy` when the pack can affect
   multi-service behavior.
10. Validate local Lite through `vexa-lite-deploy` when the pack can affect
   Lite, browser routing, dashboard config, recording, TTS, or single-container
   behavior.
11. Run `vexa-meeting-deployment-test` only when the pack reaches an actual
   external meeting boundary after synthetic checks.
12. Run `hardenloop` before the pack can be PR-ready.
13. Check evidence with `scripts/pack-evidence-check.py`.
14. Render the PR body with `scripts/render-pr-body.py`, open the PR, and keep
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
  lite/
  hardenloop/
  review.md
  pr.md
```

If a gate is intentionally not required, the pack epic must say so and the
evidence checker must record the disposition.

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
- Compose and Lite lane status;
- live/human boundary, if any;
- Hardenloop status;
- PR/evidence readiness.
