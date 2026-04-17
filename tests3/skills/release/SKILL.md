---
name: release
description: "Master orchestrator for the Vexa release cycle. Invoke this when the user talks about 'releasing', 'shipping', 'validating a release', 'the release pipeline', 'where are we in the release', 'what's next', 'run the whole cycle' — or when you need to pick the correct next stage in the numbered flow. This skill determines the current stage from on-disk state and points at the correct per-stage skill / command. Do NOT invoke for a specific stage the user has already named — go directly to that stage's skill (`0-groom` / `1-plan` / `2-provision` / `3-develop` / `4-deploy` / `5-iterate` / `6-full` / `7-human` / `8-ship` / `9-teardown`)."
---

## You are the orchestrator

Ten stages, one master flow. Each stage has its own skill. Your job: determine the current stage and direct the user to the correct per-stage skill or make command. Refuse to skip stages.

## Stage map

| # | Stage | Skill | Command | Produces |
|---|-------|-------|---------|----------|
| 0 | groom | `0-groom` | *(manual + `gh issue` + Discord)* | packs of issues ready for planning |
| 1 | plan | `1-plan` | `make release-plan ID=<slug>` | `tests3/releases/<id>/scope.yaml` |
| 2 | provision | `2-provision` | `make release-provision SCOPE=$SCOPE` | `tests3/.state-{lite,compose,helm}/` |
| 3 | develop | `3-develop` | *(local code + tests)* | commits on `dev` |
| 4 | deploy | `4-deploy` | `make release-deploy SCOPE=$SCOPE` | `:dev` images on every VM |
| 5 | iterate | `5-iterate` | `make release-iterate SCOPE=$SCOPE` | scope-filtered report (dev loop) |
| 6 | full | `6-full` | `make release-full SCOPE=$SCOPE` | automated gate: fresh-reset + full matrix |
| 7 | human | `7-human` | `make release-human-sheet SCOPE=$SCOPE` + edit + `release-human-gate` | signed-off `human-checklist.md` |
| 8 | ship | `8-ship` | `make release-ship SCOPE=$SCOPE` | dev merged to main, `:latest` promoted |
| 9 | teardown | `9-teardown` | `make release-teardown SCOPE=$SCOPE` | infra destroyed |

Stages 2 and 3 run **in parallel**. Every other stage gates on the previous.

## Detecting the current stage

Run these checks top-to-bottom; the first miss is your current stage:

```bash
# 0. Is there a scope file?
if [ -z "${SCOPE:-}" ] || [ ! -f "$SCOPE" ]; then
    # Before 1 — are there fresh issues to groom?
    GH_OPEN=$(gh issue list --repo Vexa-ai/vexa --state open --json number --jq length 2>/dev/null)
    echo "NEXT: 0-groom (issues open: $GH_OPEN) → then 1-plan"
    exit 0
fi

# 1. Is every mode in the scope provisioned?
python3 - <<'PY'
import os, yaml
s = yaml.safe_load(open(os.environ["SCOPE"]))
for m in s["deployments"]["modes"]:
    ip = f"tests3/.state-{m}/vm_ip" if m != "helm" else "tests3/.state-helm/lke_node_ip"
    if not os.path.isfile(ip):
        print(f"NEXT: 2-provision ({m} not up)"); exit()
PY

# 2. Has :dev been built and deployed since the last commit?
HEAD=$(git rev-parse --short HEAD)
LAST_TAG=$(cat deploy/compose/.last-tag 2>/dev/null || echo none)
[ "$LAST_TAG" = "none" ] && echo "NEXT: 3-develop or 4-deploy (no :dev tag yet)"

# 3. Is there a report for the current tag?
if [ ! -f "tests3/reports/release-${LAST_TAG}.md" ]; then
    echo "NEXT: 5-iterate (no report for $LAST_TAG)"
    exit 0
fi

# 4. Did release-full pass?
grep -q "Release gate PASSED" "tests3/reports/release-${LAST_TAG}.md" 2>/dev/null \
    || { echo "NEXT: 6-full (automated gate red)"; exit 0; }

# 5. Is there a human checklist?
CHECKLIST="$(dirname "$SCOPE")/human-checklist.md"
if [ ! -f "$CHECKLIST" ]; then
    echo "NEXT: 7-human (no checklist yet)"; exit 0
fi

# 6. Is it signed off?
UNCHECKED=$(grep -c '^- \[ \]' "$CHECKLIST" || echo 0)
if [ "$UNCHECKED" != "0" ]; then
    echo "NEXT: 7-human ($UNCHECKED unchecked — edit $CHECKLIST)"; exit 0
fi

# 7. Did this commit pass the release status?
SHA=$(git rev-parse origin/main 2>/dev/null || git rev-parse HEAD)
STATE=$(gh api "repos/Vexa-ai/vexa/commits/$SHA/statuses" --jq '.[] | select(.context=="release/vm-validated") | .state' 2>/dev/null | head -1)
[ "$STATE" != "success" ] && echo "NEXT: 8-ship"

# 8. Any .state-<mode>/vm_id still around?
[ -f tests3/.state-lite/vm_id ] || [ -f tests3/.state-compose/vm_id ] || [ -f tests3/.state-helm/lke_id ] \
    && echo "NEXT: 9-teardown"
```

Tell the user **which stage is next**, **which command to run**, and **why that's the next step** (e.g. "because webhook-status-fast-path is still ❌ fail on compose in the latest report"). Don't just recite the stage list — diagnose.

## Ground rules

1. **One command per stage.** If the user wants to do something mid-release that isn't a `make release-*` target, STOP. Either (a) they're in the wrong stage, or (b) we have a drift — file it as a follow-up.
2. **Scope is the contract.** Written once at stage 1, read by every stage after. Add new issues mid-cycle yes; changing existing `proves:` bindings no.
3. **Both gates before ship.** Stage 6 (automated) AND stage 7 (human) must be green. `8-ship` enforces this.
4. **Teardown happens last.** Don't destroy VMs before ship — you lose the validated state if ship fails.

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
