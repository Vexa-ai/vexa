---
name: pack
pipeline_index: 1
pipeline_name: 1-pack
description: Convert raw Vexa GitHub issues and PRs into atomic pack epic issues. Use when Codex needs to group raw issue intake into coherent, isolated packs with CEO/CTO/user outcomes, blast radius, validation gates, milestone assignment, and PR readiness contracts. This skill stops after creating or drafting pack epics; it must not create branches, worktrees, code changes, or deployments.
---

# 1. Pack

Pipeline index: `1`

Sequence: `pack` -> `develop` -> `release`

## Purpose

Turn raw GitHub issues/PRs into atomic pack epics. A pack is a standalone
engineering/business update that can later be delivered in isolation by the
`develop` skill and stitched into a release by the `release` skill.

Hard stop: this skill does not create branches, worktrees, runtime lanes, code,
or PRs.

## Inputs

Use one of:

- GitHub milestone: accepted release/initiative intake.
- Explicit issue/PR numbers.
- GitHub search query when the human asks for discovery.

Default repo: `Vexa-ai/vexa`.

## Workflow

1. Collect raw issues/PRs with `scripts/collect-issues.py`.
2. Group by outcome cohesion with `scripts/propose-packs.py`.
3. Render pack epic bodies with `scripts/render-epic-body.py`.
4. Dry-run by default. Create/update GitHub pack epics only when explicitly
   applying with `scripts/upsert-pack-epics.sh --apply`.
5. Write local run evidence under `.agents/packs/_intake/<run-id>/` and, once
   pack ids exist, copy each rendered epic/report into
   `.agents/packs/<pack-id>/pack-intake/`.

## Pack Grouping Rule

Group by one coherent outcome, not by code ownership or issue count. A valid
pack has one business outcome, one engineering invariant, and one user-visible
promise that can be validated together.

If an issue does not fit a coherent pack, mark it as out-of-scope or
needs-triage in the report. Do not force-fit it.

## Pack Epic Contract

Every pack epic issue body must include:

- CEO outcome
- CTO outcome
- User outcome
- Included raw issues/PRs
- Explicitly out of scope
- Blast radius
- Data/schema/API/public-contract decisions
- Isolation requirements
- Compose validation gate
- Lite validation gate
- Synthetic validation gate
- Live/human validation gate, only if needed
- PR readiness checklist
- Stitching risk notes

Use `references/pack-epic-template.md` as the body contract.

## Evidence

For each picking run, write:

```text
.agents/packs/_intake/<run-id>/
  issues.json
  pack-proposals.json
  bodies/
  report.md
```

## Safety

- Do not mutate GitHub unless the human asked to create/update pack epics and the
  command uses `--apply`.
- Do not use or recreate `tests3`.
- Do not infer release acceptance. Pack epics are design artifacts until later
  delivered and reviewed.
