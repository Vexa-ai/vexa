# Agent Operating Rules

## Pack-Oriented Pipeline Ownership

`.agents` has three top-level pipeline skills:

- `1-pack` / `pack`: raw GitHub issues/PRs to atomic pack epic issues. Full stop after
  pack epics are created or drafted.
- `2-develop` / `develop`: one accepted pack epic to isolated worktree/runtimes, evidence,
  review, and PR.
- `3-release` / `release`: accepted pack PRs to stitched candidate, local validation,
  throwaway stage, and sign packet.

Each pipeline skill declares `pipeline_index` and `pipeline_name` in its
frontmatter. The executable skill name stays unprefixed so user commands remain
short, while the index records the required sequence.

Pack implementation only happens through `develop`. Release stitching only
consumes accepted pack PRs. Do not make hidden stitch-time code changes; if the
stitched candidate needs code, route the finding back into the correct pack PR
or create a new pack.

## Evidence Roots

Pack evidence lives under:

```text
.agents/packs/<pack-id>/
```

Release evidence lives under:

```text
.agents/releases/<release>/
```

Do not use `tests3` as release or pack evidence. Helpful checks must move into
product tests, deploy workflows, or dedicated `.agents/skills/*` scripts before
they count.

## Operation Timing Log

Every release/debug run must keep a wall-clock operation ledger so future agents
can see where time was actually spent instead of rediscovering the same path.

Write operation spans as JSONL under the active pack or release:

```text
.agents/packs/<pack-id>/ops/ops.jsonl
.agents/releases/<release>/ops/ops.jsonl
```

Create the `ops/` directory if it does not exist. Append one JSON object per
meaningful operation, especially anything that takes more than 10 seconds, any
failed hypothesis, any rebuild/redeploy, any live meeting run, any human wait,
and any debug packet. Use UTC timestamps and duration in milliseconds.

Required fields:

```json
{
  "op_id": "20260523T121455Z-lite-build-webhookfix",
  "parent_id": null,
  "release": "0.10.6.2.1",
  "skill": "vexa-lite-deploy",
  "category": "build",
  "name": "build Lite image with webhook cache fix",
  "started_at": "2026-05-23T12:14:55Z",
  "ended_at": "2026-05-23T12:28:10Z",
  "duration_ms": 795000,
  "status": "pass",
  "command": "make -C deploy/lite build TAG=0.10.6.2.1-260523-webhookfix",
  "evidence": [
    ".agents/releases/0.10.6.2.1/lite/build-webhookfix/build.log"
  ],
  "hypothesis": "refreshed image should include gateway webhook cache invalidation",
  "result": "image built; not deployed",
  "next": "deploy refreshed Lite lane and rerun webhook delivery"
}
```

Use these category values unless a new category is genuinely clearer:

- `inspect`
- `edit`
- `test`
- `build`
- `deploy`
- `browser-proof`
- `live-meeting`
- `wait-human`
- `wait-service`
- `debug`
- `cleanup`
- `decision`

Keep evidence paths relative to the repo when possible. Record the exact command
shape, but redact secrets, tokens, cookies, webhook secrets, signed URLs, API
keys, and private meeting URLs. If the command cannot be safely recorded, write a
redacted command plus a short `redaction_reason`.

An operation span is not a substitute for the release `state.md`; the ledger is
the raw timing record. Pack PR bodies and release `state.md` / sign packets
remain the canonical summary and decision surfaces.

Preferred helper:

```bash
.agents/skills/release/scripts/oplog-run.sh \
  --release <release> \
  --skill <skill-name> \
  --category <category> \
  --name "<human-readable operation>" \
  --out .agents/releases/<release>/ops/<operation-evidence-dir> \
  --hypothesis "<what this operation is testing or trying to prove>" \
  --result "<short result, if known up front>" \
  --next "<next action this enables>" \
  -- <command> [args...]
```

For pack work, use the same helper with a pack log file override:

```bash
.agents/skills/release/scripts/oplog-run.sh \
  --release <release> \
  --skill develop \
  --category <category> \
  --name "<human-readable operation>" \
  --out .agents/packs/<pack-id>/ops/<operation-evidence-dir> \
  --log-file .agents/packs/<pack-id>/ops/ops.jsonl \
  --hypothesis "<what this operation is testing or trying to prove>" \
  --next "<next action this enables>" \
  -- <command> [args...]
```

For browser-tool, connector, or other non-shell operations that cannot be
wrapped directly, append a manual span with the same helper using `--manual`,
`--duration-ms`, `--status`, and `--evidence`. Do not leave those invisible just
because they were not shell commands.

## Timing-Led Debug Optimization

Before starting any deep debug loop, rebuild, redeploy, or repeated validation
run, inspect the active release operation ledger and use it to choose the
smallest next action.

First identify the largest wall-clock costs:

```bash
jq -s 'sort_by(.duration_ms // 0) | reverse | .[:20]' \
  .agents/releases/<release>/ops/ops.jsonl
```

Then group time by operation category:

```bash
jq -s '
  group_by(.category) |
  map({
    category: .[0].category,
    count: length,
    total_minutes: ((map(.duration_ms // 0) | add) / 60000)
  }) |
  sort_by(.total_minutes) | reverse
' .agents/releases/<release>/ops/ops.jsonl
```

Also look for repeated failed hypotheses or repeated commands:

```bash
jq -r 'select(.status != "pass") | [.category, .name, .hypothesis, .result] | @tsv' \
  .agents/releases/<release>/ops/ops.jsonl
```

Use the timing read to propose or apply time-collapse options, for example:

- replace repeated rebuilds with a no-rebuild packet when runtime state can
  classify the layer;
- replace manual browser inspection with a reusable browser-frame or DOM proof;
- parallelize independent read-only probes and evidence collection;
- preserve service health snapshots before disruptive deploys;
- prewarm or cache slow model/TTS/dependency steps when release-safe;
- record human waits separately from machine waits so blockers are visible;
- promote any repeated one-off debug command into a release skill script.

When a timing review reveals a clear optimization, record it in either the next
operation span's `next` field or the release `state.md` open-blocker/next-action
section. If an expensive path is intentionally repeated, record why it is still
necessary.

## Fallbacks

We hate fallbacks.

Do not introduce, rely on, or silently accept fallback behavior as a way to make
release validation look green. A fallback is allowed only when both conditions
are true:

1. A human explicitly decides to allow that specific fallback.
2. The fallback is for an external, nondeterministic system that Vexa does not
   control, such as Google Meet or Microsoft Teams admission, lobby behavior, UI
   timing, or other platform-side variance.

When a fallback is allowed, record the human decision, the reason, the scope of
the fallback, and the remaining risk in the relevant release state or evidence
file. Never let a fallback replace product-owned validation for deterministic
Vexa behavior.
