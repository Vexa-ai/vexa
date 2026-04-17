#!/usr/bin/env bash
# Run tests registered in tests3/test-registry.yaml for the given deployment mode.
# Each test emits a JSON artifact at .state/reports/<mode>/<name>.json
# (via test_begin/step_* in tests3/lib/common.sh).
#
# Exits 0 if every test ran and its report has status=pass.
# Exits non-zero if any test failed or its report is missing.
#
# Usage:
#   tests3/lib/run-matrix.sh <mode>                       # all cheap tests for mode
#   tests3/lib/run-matrix.sh <mode> --scope <scope.yaml>  # only tests listed in scope.proves[] for mode
set -euo pipefail

MODE="${1:?usage: run-matrix.sh <mode> [--scope <scope.yaml>]}"
shift
SCOPE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --scope) SCOPE="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

ROOT="$(git rev-parse --show-toplevel)"
T3="$ROOT/tests3"
STATE="$T3/.state"
REGISTRY="$T3/test-registry.yaml"

export MODE
export STATE

echo "$MODE" > "$STATE/deploy_mode"
mkdir -p "$STATE/reports/$MODE"

# Build the test list.
# With --scope: only the tests referenced in scope.issues[].proves[].test that include this mode,
#               plus smoke-* tiers whenever a scope check ID is referenced (so the aggregator
#               finds the check in a report). Scope filtering never runs expensive tier.
# Without --scope: every cheap-tier test whose runs_in includes this mode.
TESTS=$(python3 - <<PY
import yaml
with open("$REGISTRY") as f:
    reg = yaml.safe_load(f)
tests = reg.get("tests", {})
scope_path = "$SCOPE"
mode = "$MODE"

def want_runs_in(name):
    spec = tests.get(name) or {}
    return mode in (spec.get("runs_in") or [])

selected = set()

if scope_path:
    with open(scope_path) as f:
        scope = yaml.safe_load(f)
    for issue in (scope.get("issues") or []):
        for p in (issue.get("proves") or []):
            proof_modes = p.get("modes") or []
            if proof_modes and mode not in proof_modes:
                continue
            if "test" in p:
                selected.add(p["test"])
            elif "check" in p:
                # Check ID → include every smoke-* tier that runs in this mode.
                # Aggregator will pick out the specific check from whatever tier
                # reports it. Cheap (< 2min total) so no reason to be clever.
                for t in tests:
                    if t.startswith("smoke-") and want_runs_in(t):
                        selected.add(t)
else:
    for name, spec in tests.items():
        if spec.get("tier") != "cheap":
            continue
        if not want_runs_in(name):
            continue
        if spec.get("awaiting_retrofit"):
            continue
        selected.add(name)

# Filter to tests that actually have runs_in for this mode
for name in sorted(selected):
    spec = tests.get(name) or {}
    if not want_runs_in(name):
        continue
    if spec.get("tier") not in ("cheap",):
        # Safety: scope might list a test that isn't cheap; skip in targeted runs.
        continue
    if spec.get("awaiting_retrofit"):
        continue
    print(f"{name}\t{spec.get('script','')}")
PY
)

if [ -z "$TESTS" ]; then
    echo "  run-matrix: no cheap tests registered for mode=$MODE" >&2
    exit 0
fi

echo ""
echo "  ═══ run-matrix mode=$MODE ═══"

# Tracks failures for the final exit code.
# We keep running even if a test fails — partial reports are more useful than nothing.
FAILED_TESTS=()
MISSING_REPORTS=()

while IFS=$'\t' read -r NAME SCRIPT; do
    [ -z "$NAME" ] && continue

    # Substitute $STATE / $MODE in the script line (tests reference them).
    SCRIPT_EXPANDED="${SCRIPT//\$STATE/$STATE}"
    SCRIPT_EXPANDED="${SCRIPT_EXPANDED//\$MODE/$MODE}"

    REPORT="$STATE/reports/$MODE/${NAME}.json"

    echo ""
    echo "  ── $NAME ──"
    # Don't let a test's non-zero exit abort the matrix (set -e would).
    # We still care about the exit code for the summary.
    set +e
    ( cd "$T3" && bash -c "$SCRIPT_EXPANDED" )
    RC=$?
    set -e

    # Verify the JSON report was written.
    if [ ! -f "$REPORT" ]; then
        MISSING_REPORTS+=("$NAME")
        echo "  !! $NAME: no JSON report at $REPORT — did test_begin/test_end run?"
        continue
    fi

    # Read status from the JSON report (the authoritative verdict, not $RC).
    STATUS=$(python3 -c "
import json, sys
with open('$REPORT') as f:
    print(json.load(f).get('status','?'))
" 2>/dev/null || echo "parse_error")

    case "$STATUS" in
        pass) ;;  # Good.
        fail) FAILED_TESTS+=("$NAME") ;;
        *)    FAILED_TESTS+=("$NAME($STATUS)") ;;
    esac
done <<< "$TESTS"

echo ""
echo "  ═══ run-matrix summary mode=$MODE ═══"
if [ ${#MISSING_REPORTS[@]} -gt 0 ]; then
    echo "  missing reports: ${MISSING_REPORTS[*]}"
fi
if [ ${#FAILED_TESTS[@]} -gt 0 ]; then
    echo "  failed: ${FAILED_TESTS[*]}"
fi
if [ ${#FAILED_TESTS[@]} -eq 0 ] && [ ${#MISSING_REPORTS[@]} -eq 0 ]; then
    echo "  all tests passed"
    exit 0
fi
exit 1
