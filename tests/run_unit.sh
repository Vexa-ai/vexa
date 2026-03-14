#!/usr/bin/env bash
#
# Run all unit tests across services.
# No Docker required — tests run directly with pytest.
#
# Usage:
#   bash tests/run_unit.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PASS=0
FAIL=0
SKIP=0

run_pytest() {
    local name="$1"
    local test_dir="$2"

    if [ ! -d "$test_dir" ]; then
        printf "  %-40s SKIP (no tests/ dir)\n" "$name"
        SKIP=$((SKIP + 1))
        return
    fi

    # Check if there are any test files
    if ! find "$test_dir" -name "test_*.py" -o -name "*_test.py" 2>/dev/null | grep -q .; then
        printf "  %-40s SKIP (no test files)\n" "$name"
        SKIP=$((SKIP + 1))
        return
    fi

    printf "  %-40s" "$name"
    if python -m pytest "$test_dir" -q --tb=short 2>/dev/null; then
        echo " PASS"
        PASS=$((PASS + 1))
    else
        echo " FAIL"
        FAIL=$((FAIL + 1))
    fi
}

echo "============================================"
echo "  Vexa Unit Tests"
echo "  $(date)"
echo "============================================"
echo ""

# Service-level tests
echo "[Services]"
for service_dir in "$REPO_ROOT"/services/*/; do
    service_name=$(basename "$service_dir")
    run_pytest "$service_name" "$service_dir/tests"
done

# Libs tests
echo ""
echo "[Libraries]"
for lib_dir in "$REPO_ROOT"/libs/*/; do
    if [ -d "$lib_dir" ]; then
        lib_name=$(basename "$lib_dir")
        run_pytest "$lib_name" "$lib_dir"
    fi
done

# Top-level tests (non-integration)
echo ""
echo "[Top-level]"
if [ -d "$REPO_ROOT/tests" ]; then
    # Run only files directly in tests/ (not subdirectories like integration/, load/, etc.)
    top_level_tests=$(find "$REPO_ROOT/tests" -maxdepth 1 -name "test_*.py" 2>/dev/null)
    if [ -n "$top_level_tests" ]; then
        printf "  %-40s" "tests/"
        if python -m pytest $top_level_tests -q --tb=short 2>/dev/null; then
            echo " PASS"
            PASS=$((PASS + 1))
        else
            echo " FAIL"
            FAIL=$((FAIL + 1))
        fi
    else
        printf "  %-40s SKIP (no test files)\n" "tests/"
        SKIP=$((SKIP + 1))
    fi
fi

echo ""
echo "============================================"
echo "  Results: $PASS passed, $FAIL failed, $SKIP skipped"
echo "============================================"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
