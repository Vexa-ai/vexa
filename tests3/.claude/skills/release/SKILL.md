---
name: release
description: "Master orchestrator for the Vexa release cycle. Invoke this when the user talks about 'releasing', 'shipping', 'validating a release', 'the release pipeline', 'where are we in the release', 'what's next', 'run the whole cycle' — or when you need to pick the correct next stage. This skill determines the current stage from on-disk state and points at the correct per-stage skill / command. Do NOT invoke for a specific stage the user has already named — go directly to that stage's skill (`0-groom` / `1-plan` / `2-provision` / `3-develop` / `4-deploy` / `5-iterate` / `6-full` / `7-human` / `8-ship` / `9-teardown`)."
---

## You are the orchestrator

**The single source of truth for the protocol is `tests3/release-validation.md`.**
Read it first when anything below seems ambiguous. This skill is the dispatch
layer; the SoT is the definition.

## The loop, at a glance

```
develop → deploy → test ──fail──▶ develop
                    │
                   pass
                    ▼
                  human ──fail, new issue (stage 3b)──▶ develop
                    │
                   pass
                    ▼
                   ship ──▶ teardown
```

One loop, two exit gates (automated test + human). `release-iterate` (fast,
scope-filtered) and `release-full` (clean-reset, authoritative) are two
fidelities of the **same** test stage — not two stages.

## Stage map

| # | Stage | Skill dir | Command | Produces |
|---|-------|-----------|---------|----------|
| 0 | groom | `0-groom` | *(manual + `gh issue` + Discord)* | packs of issues ready for planning |
| 1 | plan | `1-plan` | `make release-plan ID=<slug>` | `tests3/releases/<id>/scope.yaml` |
| 2 | provision | `2-provision` | `make release-provision SCOPE=$SCOPE` | `tests3/.state-{lite,compose,helm}/` |
| 3 | develop | `3-develop` | *(local)* | commits on `dev` |
| 3b | human bug intake (entered from stage 7) | — | `make release-issue-add SOURCE=human GAP=… NEW_CHECKS=…` | new issue appended to `scope.yaml` |
| 4 | deploy | `4-deploy` | `make release-deploy SCOPE=$SCOPE` | `:dev` on every stack |
| 5 | test (fast) | `5-iterate` | `make release-iterate SCOPE=$SCOPE` | scope-filtered report |
| 5 | test (authoritative) | `6-full` | `make release-full SCOPE=$SCOPE` | fresh-reset + full matrix report + gate |
| 6 | human | `7-human` | `release-human-sheet` + edit + `release-human-gate` | signed-off checklist |
| 7 | ship | `8-ship` | `make release-ship SCOPE=$SCOPE` | dev → main, `:latest` promoted |
| 8 | teardown | `9-teardown` | `make release-teardown SCOPE=$SCOPE` | infra destroyed |

> Historical note: the on-disk skill directories are numbered 0-9 because both
> test fidelities (`5-iterate`, `6-full`) have their own skill files. The
> protocol stages are 0-8. When the user names a numbered skill, use it;
> when they ask about the "stage", point at `release-validation.md`.

Stages 2 and 3 run **in parallel**. Every other stage gates on the previous.

## Detecting the current stage

**CRITICAL — ordering rules**:
1. Always resolve paths from the git toplevel (`ROOT=$(git rev-parse --show-toplevel)`); the user's cwd may be any subdir (e.g. `tests3/releases/<id>/`).
2. Never conclude a later stage just because its output file exists. Each stage's prerequisite must also hold: checklist existing does NOT mean stage 5 passed. Walk the list top-to-bottom; first miss is the current stage.

```bash
ROOT=$(git rev-parse --show-toplevel) || { echo "not inside a git repo"; exit 2; }
cd "$ROOT"

# 0. Is there a scope file? (either $SCOPE env, or exactly one release dir)
if [ -z "${SCOPE:-}" ] || [ ! -f "$SCOPE" ]; then
    SCOPES=( tests3/releases/*/scope.yaml )
    [ -f "${SCOPES[0]}" ] && SCOPE="${SCOPES[-1]}"      # newest by sort
fi
if [ -z "${SCOPE:-}" ] || [ ! -f "$SCOPE" ]; then
    GH_OPEN=$(gh issue list --repo Vexa-ai/vexa --state open --json number --jq length 2>/dev/null)
    echo "NEXT: 0-groom (issues open: $GH_OPEN) → then 1-plan"
    exit 0
fi

# 1. Is every mode in the scope provisioned?
python3 - "$SCOPE" <<'PY'
import os, sys, yaml
s = yaml.safe_load(open(sys.argv[1]))
for m in s["deployments"]["modes"]:
    marker = f"tests3/.state-{m}/vm_ip" if m != "helm" else "tests3/.state-helm/lke_node_ip"
    if not os.path.isfile(marker):
        print(f"NEXT: 2-provision ({m} not up)"); sys.exit(1)
PY
[ $? -ne 0 ] && exit 0

# 2. Has :dev been built and deployed?
LAST_TAG=$(cat deploy/compose/.last-tag 2>/dev/null || echo none)
[ "$LAST_TAG" = "none" ] && { echo "NEXT: 4-deploy (no :dev tag yet)"; exit 0; }

# 3. Is there a report for the current tag?
REPORT="tests3/reports/release-${LAST_TAG}.md"
if [ ! -f "$REPORT" ]; then
    echo "NEXT: 5-test (no report for $LAST_TAG — run release-iterate or release-full)"
    exit 0
fi

# 4. Did stage-5 gate pass?
#    HARD PREREQ for stage 6. If gate is RED, stage 5 is current, even if
#    human-checklist.md exists from an earlier green run.
if ! grep -q "Release gate PASSED" "$REPORT"; then
    FAILS=$(grep -E '^  - ' "$REPORT" | head -3)
    echo "NEXT: 5-test (automated gate RED in $REPORT)"
    [ -n "$FAILS" ] && echo "$FAILS"
    exit 0
fi

# 5. Is there a human checklist?
CHECKLIST="$(dirname "$SCOPE")/human-checklist.md"
if [ ! -f "$CHECKLIST" ]; then
    echo "NEXT: 6-human (no checklist yet — release-human-sheet)"; exit 0
fi

# 6. Is it signed off?
UNCHECKED=$(grep -c '^- \[ \]' "$CHECKLIST" || echo 0)
if [ "$UNCHECKED" != "0" ]; then
    echo "NEXT: 6-human ($UNCHECKED unchecked — edit $CHECKLIST)"; exit 0
fi

# 7. Did this commit pass the release status?
SHA=$(git rev-parse origin/main 2>/dev/null || git rev-parse HEAD)
STATE=$(gh api "repos/Vexa-ai/vexa/commits/$SHA/statuses" --jq '.[] | select(.context=="release/vm-validated") | .state' 2>/dev/null | head -1)
[ "$STATE" != "success" ] && echo "NEXT: 7-ship"

# 8. Any .state-<mode>/vm_id still around?
[ -f tests3/.state-lite/vm_id ] || [ -f tests3/.state-compose/vm_id ] || [ -f tests3/.state-helm/lke_id ] \
    && echo "NEXT: 9-teardown"
```

Tell the user **which stage is next**, **which command to run**, and **why that's the next step** (e.g. "because webhook-status-fast-path is still ❌ fail on compose in the latest report"). Don't just recite the stage list — diagnose.

## Ground rules

1. **One command per stage.** If the user wants to do something mid-release that isn't a `make release-*` target, STOP. Either (a) they're in the wrong stage, or (b) we have a drift — file it as a follow-up.
2. **Scope is the contract.** Written once at stage 1, read by every stage after. Add new issues mid-cycle **only** via `make release-issue-add` (stage 3b).
3. **Human-found bugs require a regression check.** `source: human` issues MUST include `gap_analysis` and `new_checks[]`. Every id in `new_checks[]` MUST appear in `proves[]`. The helper enforces field presence; the aggregator gate enforces the binding. No ad-hoc fix without landing a check.
4. **Both gates before ship.** Automated test gate (stage 5) AND human gate (stage 6) must be green. `ship` enforces this.
5. **Loop cap: 3 rounds.** If human validation surfaces a new bug 3 times in a row, the scope is wrong — go back to stage 1 and split the release.
6. **Teardown happens last.** Don't destroy VMs before ship — you lose the validated state if ship fails.

## Files to know

```
tests3/
├── release-validation.md            # authoritative doc
├── test-registry.yaml               # test catalog
├── human-always.yaml                # static human checks
├── checks/registry.json             # check catalog (static/env/health/contract)
├── skills/                          # you are here
│   ├── release/                     # this skill
│   ├── 0-groom/ 1-plan/ ... 9-teardown/
├── releases/
│   ├── _template/scope.yaml         # scope schema
│   └── <id>/
│       ├── scope.yaml               # stage 1 output
│       └── human-checklist.md       # stage 7 output
└── reports/
    └── release-<tag>.md             # stages 5 + 6 outputs
```

## When in doubt

Re-read `tests3/release-validation.md`. That's the ground truth. This skill is its orchestration layer; the stage skills are its drill-downs.
