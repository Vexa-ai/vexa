---
name: release
pipeline_index: 3
pipeline_name: 3-release
description: Stitch accepted Vexa pack PRs into a release candidate, validate the stitched candidate locally and in throwaway stage, then render a sign packet. Use after pack epics have been created by `pack` and pack PRs have been delivered/reviewed by `develop`.
---

# 3. Release

Pipeline index: `3`

Sequence: `pack` -> `develop` -> `release`

## Purpose

Turn accepted, reviewed pack PRs into a stitched Vexa release candidate.

This skill does not create raw scope, implement pack code, or hide final
integration edits. It consumes pack PRs produced by `develop`.

Canonical release evidence:

```text
.agents/releases/<version>/
```

## Required Inputs

- release id;
- GitHub milestone or explicit accepted pack epic issues;
- integration branch;
- base branch, usually the last stable release tag/branch.

## Workflow

1. Read accepted pack epics and PR references with
   `scripts/read-pack-prs.py`.
2. Verify every pack has accepted status, PR identity, and required evidence
   with `scripts/verify-pack-evidence.py`.
3. Reject direct unreviewed integration commits. Only pack PR merge commits may
   enter the stitched candidate.
4. Stitch pack PRs with merge commits using `scripts/stitch-pack-prs.sh`.
5. Validate the stitched candidate locally through Compose and Lite with
   `scripts/local-stitch-validate.sh` and the existing deployment skills:
   `compose-deploy` and `vexa-lite-deploy`.
6. Run full stitched-candidate `hardenloop`.
7. Stage in throwaway infrastructure with `throwaway-infra-deploy`, validating
   Compose, Lite, and Helm lanes through their deployment skills.
8. Run `vexa-meeting-deployment-test` for live Google Meet / Microsoft Teams
   coverage only at gates that actually need external platform evidence.
9. Render the sign packet with `scripts/render-sign-packet.py`.

## Hard Gates

Release must reject:

- pack PRs without evidence;
- pack PRs with unresolved review blockers;
- direct commits on the integration branch that are not tied to accepted pack
  PR merge commits;
- missing local Compose validation for changed multi-service surfaces;
- missing local Lite validation for changed Lite/browser/self-hosted surfaces;
- missing throwaway Compose/Lite/Helm evidence for release-relevant surfaces;
- human-only claims for machine-validatable facts.

## No Hidden Stitch-Time Code

If stitching exposes a bug, stop the stitch, route the finding back into the
right pack, and have `develop` produce an updated pack PR. The release skill may
resolve merge conflicts only when the resolution is mechanical and fully tied to
the reviewed pack diffs; otherwise create a new pack.

## Operation Wall-Time Ledger

Follow `.agents/AGENTS.md` for operation timing. Release-level spans go under:

```text
.agents/releases/<version>/ops/ops.jsonl
```

Use the helper:

```bash
.agents/skills/release/scripts/oplog-run.sh \
  --release <version> \
  --skill release \
  --category <inspect|edit|test|build|deploy|browser-proof|live-meeting|wait-human|wait-service|debug|cleanup|decision> \
  --name "<operation name>" \
  --out .agents/releases/<version>/ops/<operation-evidence-dir> \
  --hypothesis "<what this proves>" \
  --next "<what this unlocks>" \
  -- <command> [args...]
```

Before rebuilds, redeploys, deep debug, or repeated validation loops, inspect:

```bash
.agents/skills/release/scripts/oplog-summary.sh --release <version>
```

## Fast Debug / No-Rebuild Rule

When a dashboard, transcript, WebSocket, webhook, recording, or meeting-detail
regression is reported against an already running local or stage lane, first run
a no-rebuild debug packet. Do not rebuild images, restart containers, redeploy,
or change source until the packet classifies the failing layer.

Use the release-owned helpers:

```bash
.agents/skills/release/scripts/debug-dashboard-meeting.sh \
  --dashboard-url <dashboard-url> \
  --meeting-id <meeting-id> \
  --out .agents/releases/<version>/debug/<case-id>
```

```bash
node .agents/skills/release/scripts/dashboard-ws-frame-proof.mjs \
  --dashboard-url <dashboard-meeting-url> \
  --meeting-id <meeting-id> \
  --out .agents/releases/<version>/debug/<case-id>/browser-ws-frame-proof.json
```

Rendered transcript text, REST transcript responses, open sockets, or subscribed
acks alone are not WebSocket delivery proof. A pass requires a transcript frame
for the exact meeting id.

For completed meetings where live streams are gone, use:

```bash
.agents/skills/release/scripts/dashboard-ws-synthetic-proof.sh \
  --dashboard-url <dashboard-url> \
  --out .agents/releases/<version>/debug/<case-id>/synthetic-ws-proof
```

## Out-Of-Scope Bug Handling

If a bug is definitely outside the accepted pack scope and definitely not a
regression:

1. File or draft a Vexa OSS GitHub issue with evidence and reproduction steps.
2. Log the finding in `.agents/releases/<version>/state.md`.
3. Keep moving on release gates.

If the bug is in scope or plausibly a regression, route it back into the
responsible pack PR before stitching continues.

## Completion Response

When reporting progress, state:

- release id and integration branch;
- accepted pack PRs consumed;
- local Compose/Lite validation status;
- throwaway Compose/Lite/Helm validation status;
- live meeting/human gate status;
- sign packet path or the exact blocker.
