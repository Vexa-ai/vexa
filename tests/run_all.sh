#!/usr/bin/env bash
#
# Run unit + integration + smoke tests.
#
# Usage:
#   bash tests/run_all.sh
#
# Prerequisites:
#   - For integration and smoke tests: Docker services running

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "============================================"
echo "  Vexa Full Test Suite"
echo "  $(date)"
echo "============================================"
echo ""

OVERALL_FAIL=0

# --- Unit Tests ---
echo ">>> Running unit tests..."
if bash "$SCRIPT_DIR/run_unit.sh"; then
    echo ""
    echo ">>> Unit tests: PASS"
else
    echo ""
    echo ">>> Unit tests: FAIL"
    OVERALL_FAIL=1
fi
echo ""

# --- Integration Tests ---
echo ">>> Running integration tests..."
if [ -d "$SCRIPT_DIR/integration" ]; then
    integration_tests=$(find "$SCRIPT_DIR/integration" -name "test_*.py" 2>/dev/null)
    if [ -n "$integration_tests" ]; then
        if python -m pytest "$SCRIPT_DIR/integration/" -q --tb=short 2>/dev/null; then
            echo ">>> Integration tests: PASS"
        else
            echo ">>> Integration tests: FAIL"
            OVERALL_FAIL=1
        fi
    else
        echo ">>> Integration tests: SKIP (no test files)"
    fi
else
    echo ">>> Integration tests: SKIP (no directory)"
fi
echo ""

# --- Smoke Tests ---
echo ">>> Running smoke tests..."
if [ -f "$SCRIPT_DIR/smoke/test_full_stack.sh" ]; then
    if bash "$SCRIPT_DIR/smoke/test_full_stack.sh"; then
        echo ">>> Smoke tests: PASS"
    else
        echo ">>> Smoke tests: FAIL"
        OVERALL_FAIL=1
    fi
else
    echo ">>> Smoke tests: SKIP (script not found)"
fi
echo ""

# --- Staleness Audit ---
echo ">>> Running staleness audit..."
echo "=== Staleness Audit ==="
if python "$SCRIPT_DIR/audit/staleness_audit.py"; then
    echo ">>> Staleness audit: PASS"
else
    echo ">>> Staleness audit: FINDINGS DETECTED"
    # Don't fail the suite for staleness findings — they're informational
fi
echo ""

# --- Summary ---
echo "============================================"
if [ "$OVERALL_FAIL" -eq 0 ]; then
    echo "  All test suites passed."
else
    echo "  Some test suites failed. See output above."
fi
echo "============================================"

exit $OVERALL_FAIL
