#!/usr/bin/env bash
# Run the cheap-tier tests registered in tests3/test-registry.yaml for the given
# deployment mode. Each test emits a JSON artifact at .state/reports/<mode>/<name>.json
# (via test_begin/step_* in tests3/lib/common.sh).
#
# Exits 0 if every test ran and its report has status=pass.
# Exits non-zero if any test failed or its report is missing.
#
# Usage: tests3/lib/run-matrix.sh <lite|compose|helm>
set -euo pipefail

MODE="${1:?usage: run-matrix.sh <lite|compose|helm>}"

ROOT="$(git rev-parse --show-toplevel)"
T3="$ROOT/tests3"
STATE="$T3/.state"
REGISTRY="$T3/test-registry.yaml"

# Expose MODE to tests (needed by the $STATE/$MODE substitution in registry scripts)
export MODE
export STATE

# Make sure .state/deploy_mode matches the mode we're running (tests read it).
echo "$MODE" > "$STATE/deploy_mode"
mkdir -p "$STATE/reports/$MODE"

# Extract the list of tests to run for this mode.
# A test runs if: tier=cheap AND mode is in runs_in AND awaiting_retrofit is not true.
TESTS=$(python3 - <<PY
import sys, yaml
with open("$REGISTRY") as f:
    reg = yaml.safe_load(f)
for name, spec in reg.get("tests", {}).items():
    if spec.get("tier") != "cheap":
        continue
    if "$MODE" not in (spec.get("runs_in") or []):
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
