#!/usr/bin/env bash
# package-tests — runs `npm test` in each packages/* workspace.
#
# Step IDs:
#   transcript_rendering — packages/transcript-rendering vitest suite

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
STEP_REQUESTED="${1:-}"

echo ""
echo "  package-tests"
echo "  ──────────────────────────────────────────────"

test_begin package-tests

run_pkg_step() {
    local step="$1"
    local dir="$2"
    if [ -n "$STEP_REQUESTED" ] && [ "$STEP_REQUESTED" != "$step" ]; then
        return 0
    fi
    if [ ! -d "$ROOT_DIR/$dir" ]; then
        step_fail "$step" "package dir missing: $dir"
        return 1
    fi
    # Install quickly if lock is stale; prefer ci for reproducibility.
    (cd "$ROOT_DIR/$dir" && npm ci --silent 2>/dev/null || npm install --silent) >/dev/null 2>&1
    local out
    out=$(cd "$ROOT_DIR/$dir" && npm test 2>&1)
    local rc=$?
    if [ "$rc" -eq 0 ]; then
        local n_tests
        n_tests=$(echo "$out" | grep -oE 'Tests\s+[0-9]+ passed' | head -1)
        step_pass "$step" "${n_tests:-npm test passed}"
    else
        local tail
        tail=$(echo "$out" | tail -5 | tr '\n' ' ')
        step_fail "$step" "npm test failed — ${tail:0:200}"
    fi
}

run_pkg_step transcript_rendering packages/transcript-rendering

echo "  ──────────────────────────────────────────────"
echo ""
