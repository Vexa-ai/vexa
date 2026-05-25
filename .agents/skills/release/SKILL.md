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
5a. **Per-pack blast-radius regression suite (machine).** For each
    stitched pack, run its own blast-radius validation harness against
    the stitched stack — not against the pack's isolated lane. This is
    the only way to prove "no other pack broke this one's surface".
    Each pack's `synthetic/` evidence dir contains runners or scripts
    that exercise its blast radius; `scripts/stitched-blast-radius.sh`
    walks every accepted pack and runs each one against the stitched
    Compose + Lite. Failures here ROUTE BACK to the responsible pack;
    they never become hidden stitch-time fixes.
6. Run full stitched-candidate `hardenloop`.
7. Stage in throwaway infrastructure with `throwaway-infra-deploy`, validating
   Compose, Lite, and Helm lanes through their deployment skills.
8. Run `vexa-meeting-deployment-test` for live Google Meet / Microsoft Teams
   coverage only at gates that actually need external platform evidence.
8a. **Human eyeball delivery focused on what changed.** Render a
    pack-by-pack eyeball checklist that calls out specifically what
    surfaces each pack changes and what the human reviewer must verify
    visually in the stitched candidate (dashboard pages, audio playback,
    browser-session VNC, transcript UI, version surfaces, etc.). The
    helper `scripts/render-eyeball-checklist.sh` produces this from each
    pack epic's blast-radius declaration. The human reviewer sees one
    document, not seven.
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

## Stitch Failure Triage Protocol

A stitch deployment "fails" when any of these happen:

- merge conflict is non-mechanical (requires a code judgement call);
- any service image fails to build;
- the stitched Compose or Lite stack fails to come up healthy;
- a per-pack blast-radius regression suite fails on the stitched candidate
  but passes on that pack's isolated lane.

**Required behaviour:**

1. **Stop progressing the stitch.** Do NOT patch the stitched candidate to
   make it pass. Do NOT delete or amend pack commits in the candidate
   without routing the fix back to the responsible pack.
2. **Bisect to the responsible pack.** Re-run the failing build/deploy/test
   against each pack's branch in isolation (or against partial stitches of
   N-1, N-2 packs) to identify the smallest pack-set that reproduces the
   failure. The first pack in the merge order whose addition causes the
   failure is the responsible one.
3. **Open or update a GitHub issue on that pack's epic** with:
   - exact error / stack trace / failed assertion;
   - the partial-stitch reproduction command;
   - a finding name (e.g. `stitch-regression-<symptom>`).
4. **Push a fix commit to the responsible pack's PR.** The fix must be
   scoped to that pack's blast radius. If the fix expands scope, file a
   new pack epic and route the fix there.
5. **Rebuild + redeploy + rerun blast-radius** for that pack in isolation
   AND in the stitched candidate. Both must pass before the stitch
   resumes.
6. Log every triage cycle in `.agents/releases/<version>/state.md` with
   timestamps + the bisect path taken. The wall-time ledger
   (`.agents/releases/<version>/ops/ops.jsonl`) records each operation.

This protocol is the only path to a clean release candidate. It is
strictly forbidden to:

- silently downgrade a failing pack to a partial / feature-flagged ship;
- monkey-patch the stitched candidate with code not present in any pack PR;
- mark a pack `status:ready-for-stage` whose blast-radius suite fails on
  the stitched stack;
- ship a release candidate while any pack's blast-radius validation is
  red.

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
