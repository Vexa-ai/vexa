# Release Validation — Single Source of Truth

> **This file is the protocol.** Every other doc (`skills/*/SKILL.md`,
> per-release scope files, the Makefile comments) defers to it. If something
> here conflicts with the code, **the code is wrong**: fix the code, or fix
> this file — never both silently.

A release is a scope-driven, 10-stage process. Each stage is **one command**.
No ad-hoc runs. No fallbacks. No legacy paths.

---

## Flow diagram

One loop, two gates. `release-iterate` (fast, scope-filtered) and
`release-full` (clean-reset, authoritative) are the same stage 5 at two
fidelities — not separate stages.

```
  0 groom  →  1 plan (scope.yaml)
                │
        ┌───────┴────────┐
        ▼                ▼      parallel
  2 provision       3 develop
        └───────┬────────┘
                ▼
           4 deploy ◀──────────┐
                │              │
                ▼              │
           5 test ─── fail ────┘
                │
              pass
                ▼
           6 human ─── fail ──▶ 3b release-issue-add
                │               (SOURCE=human: GAP + NEW_CHECKS required)
              pass                        │
                ▼                         ▼
           7 ship                   3 develop (implement the check, loop)
                │
                ▼
           8 teardown
```

**3-round cap** on human-found bugs in one cycle — after that, split the scope.

---

## Protocol invariants

These hold across every release. Violating any of them is a bug in the
pipeline, not "just this release".

1. **Scope is the contract.** Every release has one `tests3/releases/<id>/scope.yaml`.
   The schema is `tests3/releases/_template/scope.yaml`. Stages 2–9 all read
   this single file. If you need to change what's validated, edit the scope,
   never the runner.

2. **One command per stage.** If you find yourself doing a stage by hand, the
   command is missing — file a gap, don't work around it.

3. **No fallbacks.** If a report JSON is missing, the test didn't run. If a
   check isn't in the registry, it isn't a lock. No "parse stdout if JSON is
   absent", no "assume pass if unknown". Structured output is the only truth.

4. **Every human-found bug lands a regression check BEFORE the release ships.**
   Issues with `source: human` **require** both `gap_analysis` (why the matrix
   missed it) and `new_checks` (IDs that catch it next time). Every id in
   `new_checks[]` must also appear in `proves[]`. The aggregator gate enforces
   this — `release-ship` cannot run unless both are satisfied.

5. **Per-feature confidence gates the merge.** Each feature README frontmatter
   declares `gate.confidence_min`. `release-full` fails if any feature is below
   its threshold OR any `required_mode` shows a fail for a scope issue.

6. **Both gates must pass.** Automated gate (stage 6) + human gate (stage 7).
   Neither is optional. `release-ship` checks both.

7. **Clean state for the authoritative gate.** `release-full` always resets
   every mode before running. `release-iterate` skips reset (fast-feedback) —
   it is *not* authoritative.

8. **Repo scope.** Only files under `tests3/` and the repo's top-level
   `Makefile` implement this protocol. Cross-repo fixes live in the feature
   owners' repos, bound to this one via the registry.

---

## Stage table

| # | Stage | Command | Produces | Gate |
|---|-------|---------|----------|------|
| 0 | groom | *(skill)* `0-groom` | Short list: candidate issues (GH + Discord + internal) | — |
| 1 | plan | `make release-plan ID=<slug>` | `tests3/releases/<id>/scope.yaml` | — |
| 2 | provision | `make release-provision SCOPE=…` | VMs + LKE cluster; `tests3/.state-<mode>/*` | — |
| 3 | develop | *(local; commits on `dev`)* | Code + tests + feature DoDs | — |
| 3b | *(human bug intake, entered from 6)* | `make release-issue-add SOURCE=human GAP=… NEW_CHECKS=…` | Appends an issue to `scope.yaml`; blocks ship until the new check passes | schema enforced by helper + aggregator |
| 4 | deploy | `make release-deploy SCOPE=…` | `:dev` pushed; stacks up on every mode | — |
| 5 | test | `make release-iterate SCOPE=…` (fast) **or** `make release-full SCOPE=…` (authoritative) | Per-test JSON reports; `tests3/reports/release-<tag>.md`; feature DoD tables | per-feature confidence + scope required_modes (both commands use the same gate) |
| 6 | human | `make release-human-sheet SCOPE=…` → fill in → `make release-human-gate SCOPE=…` | `tests3/releases/<id>/human-checklist.md` with hash-tagged ticks | every `- [ ]` must be `- [x]` |
| 7 | ship | `make release-ship SCOPE=…` | `release/vm-validated` GH status; PR dev→main; `:dev → :latest` promotion | both gates (5 + 6) must have passed |
| 8 | teardown | `make release-teardown SCOPE=…` | Destroyed VMs + cluster | — |

**Stage 5 has two commands, one gate.** `release-iterate` is the fast
inner-loop variant (scope-filtered, dirty state). `release-full` is the
authoritative exit variant (clean-reset, full cheap-tier matrix). They
share the aggregator + the gate. Protocol-wise they are **one stage**.
You iterate until green, then run full once to confirm clean-state
doesn't regress, then hand off to stage 6.

### Stage 3b — human bug intake (the iteration loop)

When a human spots something in stage 7 (or anywhere post-iterate), it does
**not** get fixed ad-hoc. It goes through the protocol:

```bash
make release-issue-add \
  SCOPE=tests3/releases/<id>/scope.yaml \
  ID=<bug-slug> \
  SOURCE=human \
  PROBLEM="observable symptom" \
  HYPOTHESIS="root cause theory" \
  GAP="why the automated matrix missed this" \
  NEW_CHECKS="CHECK_ID_1,CHECK_ID_2"   # or test:step form
```

The helper **refuses** to write the issue if `GAP` or `NEW_CHECKS` is empty
for `SOURCE=human`. The aggregator gate additionally verifies every
`NEW_CHECKS` id is wired into `proves[]`. Together this enforces the rule:
**no human-found bug closes without a regression check that would have caught
it.**

After intake:
1. Implement the new check in `tests3/checks/registry.json` (+ `tests3/checks/run`).
2. `make release-deploy SCOPE=…` (so the VMs have the new registry).
3. `make release-iterate SCOPE=…` to confirm the check passes.
4. `make release-full SCOPE=…` — fresh-reset re-validation is authoritative.
5. `make release-human-sheet --force` — regenerates, **preserves** checkmarks
   for unchanged items (via per-item hash markers).
6. `make release-human-gate` — final ok.

**Loop cap**: 3 rounds. More than that signals the scope is wrong — go back
to stage 1 and split the release.

---

## Per-release artifacts

All under `tests3/releases/<release_id>/`:

| File | Stage | Shape |
|------|-------|-------|
| `scope.yaml` | 1 (edited through 5) | Issue list, modes, proves, gap_analysis, new_checks |
| `human-checklist.md` | 7 | One `- [ ]` / `- [x]` per action; items hash-tagged so regeneration preserves state |

Plus, global outputs:

| File | Written by | Purpose |
|------|-----------|---------|
| `tests3/reports/release-<tag>.md` | stage 6 | Aggregated per-feature report; committed to git |
| `features/<name>/README.md` (DoD table rows) | stage 6 | Auto-rewritten with live evidence; idempotent |
| `tests3/.state-<mode>/reports/<mode>/*.json` | stages 5, 6 | Per-test structured output; the only source of truth |

---

## Schema references

- **Scope schema**: `tests3/releases/_template/scope.yaml`
- **Test registry**: `tests3/test-registry.yaml` (which tests run in which modes, and their step contracts)
- **Registry checks**: `tests3/checks/registry.json` (static / env / health / contract tiers)
- **Feature DoDs**: `features/*/README.md` frontmatter `tests3.dods:` and `tests3.gate:`
- **Human-always checks**: `tests3/human-always.yaml` (the static ALWAYS block of every checklist)

---

## Branch model

- **`main`** — stable, `IMAGE_TAG=latest`. Matches `:latest` on DockerHub.
- **`dev`** — active development, `IMAGE_TAG=dev`. Builds publish to `:dev`.

`release-ship` overwrites `env-example` back to `IMAGE_TAG=latest` on main,
pushes the `release/vm-validated` commit status (required by branch
protection), and promotes `:dev → :latest` for every image.

---

## Static regression locks

Independent of the release cycle — run on every commit via the
`smoke-static` tier inside stages 5 and 6. See `tests3/checks/registry.json`
tier=`static`. Every past bug lands one here.

---

## What this doc replaces

- The old `tests3/VALIDATION.md` (archived).
- The per-stage `tests3/.claude/skills/N-*/SKILL.md` are still there, but they are
  thin command-wrappers that point at this doc. When the protocol changes,
  change this doc first, then update the skills.
- The Makefile comments on `release-*` targets should link here, not
  re-describe the flow.

---

## Changelog

Append one row per protocol-level change. Issue-level scope edits don't
belong here — they live in each release's `scope.yaml`.

| Date       | Change |
|------------|--------|
| 2026-04-17 | Initial 10-stage scope-driven protocol.  |
| 2026-04-17 | Stage 3b (human bug intake): `release-issue-add` enforces `gap_analysis` + `new_checks` for `source: human`; aggregator gate verifies bindings; `human-checklist.md` now hash-tags items so regeneration preserves ticks. 3-round iteration cap documented. |
