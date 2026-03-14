#!/usr/bin/env bash
#
# Load test orchestrator — runs all load tests and generates a summary report.
#
# Usage:
#   bash tests/load/run_load_test.sh [--vus N] [--duration S]
#
# Options:
#   --vus N       Number of virtual users for concurrent tests (default: 5)
#   --duration S  Duration in seconds for memory tests (default: 300)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RESULTS_DIR="$SCRIPT_DIR/results"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

VUS=5
DURATION=300

while [[ $# -gt 0 ]]; do
    case $1 in
        --vus) VUS="$2"; shift 2 ;;
        --duration) DURATION="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

mkdir -p "$RESULTS_DIR"

echo "============================================"
echo "  Vexa Load Test Suite"
echo "  $(date)"
echo "  VUs: $VUS | Duration: ${DURATION}s"
echo "============================================"
echo ""

REPORT_FILE="$RESULTS_DIR/report_${TIMESTAMP}.txt"
PASS=0
FAIL=0

run_test() {
    local name="$1"
    local cmd="$2"

    echo "--- $name ---"
    echo "--- $name ---" >> "$REPORT_FILE"

    if eval "$cmd" >> "$REPORT_FILE" 2>&1; then
        echo "  PASS"
        echo "  RESULT: PASS" >> "$REPORT_FILE"
        PASS=$((PASS + 1))
    else
        echo "  FAIL (see $REPORT_FILE for details)"
        echo "  RESULT: FAIL" >> "$REPORT_FILE"
        FAIL=$((FAIL + 1))
    fi
    echo "" >> "$REPORT_FILE"
}

# Test 1: Single request baseline
run_test "Transcription: Single Request Baseline" \
    "cd '$REPO_ROOT' && python '$SCRIPT_DIR/transcription_service.py' --mode single --output '$RESULTS_DIR/single_${TIMESTAMP}.json'"

# Test 2: Concurrent throughput
run_test "Transcription: Concurrent ($VUS VUs)" \
    "cd '$REPO_ROOT' && python '$SCRIPT_DIR/transcription_service.py' --mode concurrent --vus $VUS --output '$RESULTS_DIR/concurrent_${TIMESTAMP}.json'"

# Test 3: Memory leak detection
run_test "Transcription: Memory Leak Detection ($VUS VUs, ${DURATION}s)" \
    "cd '$REPO_ROOT' && python '$SCRIPT_DIR/transcription_service.py' --mode memory --vus $VUS --duration $DURATION --output '$RESULTS_DIR/memory_${TIMESTAMP}.json'"

# Summary
echo ""
echo "============================================"
echo "  Summary"
echo "  Passed: $PASS | Failed: $FAIL"
echo "  Report: $REPORT_FILE"
echo "  Results: $RESULTS_DIR/*_${TIMESTAMP}.json"
echo "============================================"

echo "" >> "$REPORT_FILE"
echo "SUMMARY: Passed=$PASS Failed=$FAIL" >> "$REPORT_FILE"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
