---
name: release
description: "Invoke this skill for ANY release work on the Vexa repo: planning a release from GitHub issues, provisioning deployments, iterating on targeted tests against live VMs, running the full authoritative validation, filling the human validation checklist, merging to main, or tearing down the infra afterward. This is the single entry point for the 7-stage commanded release process defined in /home/dima/dev/vexa/tests3/release-validation.md. Use when the user says things like 'let's ship', 'release this', 'validate', 'start a release', 'run the pipeline', 'provision VMs', 'iterate on the fix', 'gate check', 'merge to main', 'tear down', 'new release cycle', or refers to scope.yaml / release-validation.md / validate-<mode> / the 7-stage flow."
---

## What this replaces

The old `master`, `dev`, `dev-init`, `dev-develop`, `dev-develop-report`,
`dev-finalize`, `dev-cleanup`, and `deployment` skills. They described an
ad-hoc flow that drifted from run to run. The new flow is **7 explicit
commands**, one per stage, driven by a per-release scope file. No drift:
if you find yourself running `docker compose …` ad-hoc during a release,
stop and look up the correct `make release-*` command.

## The 7 stages

Every release cycle on the `vexa` repo follows this sequence. Each stage
is ONE command. Refuse to deviate.

| # | Stage       | Command                                             | When           |
|---|-------------|-----------------------------------------------------|----------------|
| 1 | plan        | `make release-plan ID=<slug>`                       | once, up front |
| 2 | provision   | `make release-provision SCOPE=$SCOPE`               | after plan     |
| 3 | develop     | *(local dev — edit code + tests + frontmatter)*     | parallel to #2 |
| 4 | deploy      | `make release-deploy SCOPE=$SCOPE`                  | after 2 + 3    |
| 5 | iterate     | `make release-iterate SCOPE=$SCOPE`                 | loop until green |
| 6 | full + human | `make release-full SCOPE=$SCOPE` + `make release-human-sheet SCOPE=$SCOPE` | after iterate green |
| 7 | ship        | `make release-ship SCOPE=$SCOPE`                    | both gates green |
| 8 | teardown    | `make release-teardown SCOPE=$SCOPE`                | after ship     |

Set `export SCOPE=tests3/releases/<id>/scope.yaml` once after stage 1; every
subsequent command reads it.

## Stage 1: plan

Create the scope file. The scope is the contract between the dev work and
the validation — it lists every issue in flight, its root-cause hypothesis,
the commits that fix it, and **which test steps / registry checks would
go fail→pass once the fix is correct**. It also pins which deployments
must prove the release out (lite / compose / helm — default all three).

```bash
cd ~/dev/vexa
make release-plan ID=260417-webhooks-dbpool     # writes tests3/releases/260417-webhooks-dbpool/scope.yaml
$EDITOR tests3/releases/260417-webhooks-dbpool/scope.yaml
export SCOPE=tests3/releases/260417-webhooks-dbpool/scope.yaml
```

Fill in for each issue:

- `problem`: user-visible symptom.
- `hypothesis`: root-cause theory.
- `fix_commits`: git SHAs (grow as you commit).
- `proves`: list of `{test, step, modes}` or `{check, modes}` bindings.
- `required_modes`: deployments that must go green for this issue.
- `human_verify`: list of `{mode, do, expect}` steps for the human sheet.

Source issues from `gh issue list --repo Vexa-ai/vexa` and human reports.

## Stage 2: provision (parallel with stage 3)

Spins up every deployment listed in `scope.deployments.modes` in parallel.
Takes ~10 min — kick it off early so it runs while you develop.

```bash
make release-provision SCOPE=$SCOPE
```

Creates `tests3/.state-{lite,compose,helm}/` with per-deployment credentials
(vm_ip, kubeconfig, etc). Reruns are idempotent — will reuse existing infra.

## Stage 3: develop (local, out-of-band)

Edit code + tests + frontmatter in parallel with the provision job. Any new
DoD goes in the feature's `features/*/README.md` frontmatter `tests3.dods:`
block. Any new test step goes in `tests3/test-registry.yaml` AND as a
`step_pass`/`step_fail` call in the test script. `tests3/lib/common.sh`
exposes `test_begin` / `step_pass` / `step_fail` / `step_skip` / `test_end`.

No stdout parsing — JSON reports under `.state/reports/<mode>/<test>.json`
are the only source of truth. Commit to `dev` when the local unit tests pass.

## Stage 4: deploy

Builds a fresh `:dev` timestamp tag, publishes to DockerHub, pulls on each
provisioned deployment, restarts the stack (keeps volumes).

```bash
make release-deploy SCOPE=$SCOPE
```

## Stage 5: iterate (fast inner loop)

Runs **only** the tests the scope's `proves[]` references for each mode.
Fast (~2-3 min), scope-filtered. Produces a report with a "Scope status"
section showing per-issue verdicts across each mode.

```bash
make release-iterate SCOPE=$SCOPE
# → tests3/reports/release-<tag>.md
```

Loop: if something is red → edit code → commit → `make release-deploy
SCOPE=$SCOPE && make release-iterate SCOPE=$SCOPE` → repeat.

Reports show per-feature confidence. The iterate step does NOT gate merge —
it's the dev loop. Gating happens at stage 6.

## Stage 6: full + human

Two gates; both must pass before ship.

### 6a. full automated (fresh state)

```bash
make release-full SCOPE=$SCOPE
```

This does: reset every deployment (wipe stack + volumes, keep VMs/cluster)
→ redeploy latest `:dev` → run the full cheap-tier matrix across every
mode → aggregate → gate-check. The gate fails if:

- any feature's confidence drops below its `gate.confidence_min` in its
  README frontmatter (or below 100% if the feature is listed in
  `scope.strict_features`), OR
- any `scope.issues[]` has a `fail` status in one of its `required_modes`.

### 6b. human checklist

```bash
make release-human-sheet SCOPE=$SCOPE
# → tests3/releases/<id>/human-checklist.md
```

The generated checklist has two parts:
- **ALWAYS**: static checks from `tests3/human-always.yaml` — dashboard
  loads, bot joins a real meeting, transcripts persist, no error spike in
  logs. Applies every release regardless of scope.
- **THIS RELEASE**: scope-specific checks from `scope.issues[].human_verify`
  (one line per `mode × do × expect`).

Edit the markdown, change each `- [ ]` to `- [x]` as you verify. Any
unchecked box blocks ship (enforced by `release-human-gate`).

## Stage 7: ship

Refuses to run unless both gates are green.

```bash
make release-ship SCOPE=$SCOPE
```

Does: verify human checklist → push `release/vm-validated` GitHub status
→ PR dev→main → merge → fix `env-example` on main (`IMAGE_TAG=latest`) →
promote every image `:dev → :latest`.

## Stage 8: teardown

Destroys every VM + LKE cluster. Irreversible — only run after ship is
green.

```bash
make release-teardown SCOPE=$SCOPE
```

## Escape hatches

- **Cold-start full pipeline**: `make release-test SCOPE=$SCOPE` runs
  provision + deploy + full in sequence. Useful for one-shot validation
  of an existing scope.
- **Partial teardown**: `make -C tests3 vm-destroy STATE=tests3/.state-<mode>`
  removes a single deployment.
- **Debug a live deployment**: SSH info is in `tests3/.state-<mode>/vm_ip`;
  kubeconfig is in `tests3/.state-helm/lke_kubeconfig_path`.
- **Inspect reports**: `tests3/reports/release-<tag>.md` is the current
  authoritative report; `features/*/README.md` DoD tables are auto-written
  from the same evidence.

## Key files

```
tests3/
├── release-validation.md              # the doc — single source of truth
├── test-registry.yaml                 # test catalog (tier, runs_in, steps, features)
├── human-always.yaml                  # static human checks (every release)
├── checks/registry.json               # static + env + health + contract checks
├── lib/
│   ├── common.sh                      # test_begin/step_* helpers
│   ├── run-matrix.sh                  # run tests for a mode (±--scope)
│   ├── aggregate.py                   # build release report + update feature READMEs
│   ├── human-checklist.py             # generate + gate the human sheet
│   └── reset/                         # per-mode reset scripts
└── releases/
    ├── _template/scope.yaml           # scope file template
    └── <id>/
        ├── scope.yaml                 # this release's issues + bindings
        └── human-checklist.md         # generated at stage 6b
```

## Escalation

If the release keeps failing the same check across modes: stop iterating.
The hypothesis is wrong. Go back to stage 1 — re-read the problem
statement, write a new hypothesis as a new `issues:` entry. Don't
workaround in the test.
