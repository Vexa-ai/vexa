#!/usr/bin/env bash
set -euo pipefail

# Helm template validation test (no cluster required)
# Usage: ./deploy/helm/tests/test_template.sh

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

PASS=0
FAIL=0
RESULTS=""

check() {
  local name="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    RESULTS+="PASS: $name\n"
    ((PASS++))
  else
    RESULTS+="FAIL: $name\n"
    ((FAIL++))
  fi
}

echo "=== Helm Template Validation Test ==="
echo "Started: $(date -Iseconds)"
echo ""

# Find charts
CHARTS_DIR="$SCRIPT_DIR/charts"
if [ ! -d "$CHARTS_DIR" ]; then
  echo "FAIL: charts/ directory not found at $CHARTS_DIR"
  exit 1
fi

# Test each chart
for chart_dir in "$CHARTS_DIR"/*/; do
  chart_name=$(basename "$chart_dir")
  echo ">>> Testing chart: $chart_name"

  # Template render
  check "$chart_name: helm template" helm template "test-$chart_name" "$chart_dir"

  # Lint
  check "$chart_name: helm lint" helm lint "$chart_dir"

  # Dry run (requires cluster connection)
  export KUBECONFIG="${KUBECONFIG:-/home/dima/.kube/config}"
  if kubectl cluster-info >/dev/null 2>&1; then
    check "$chart_name: dry-run install" helm install "test-$chart_name" "$chart_dir" --dry-run --generate-name
  else
    RESULTS+="SKIP: $chart_name: dry-run (no cluster)\n"
  fi

  echo ""
done

# Verify chart dependencies
for chart_dir in "$CHARTS_DIR"/*/; do
  chart_name=$(basename "$chart_dir")
  if [ -f "$chart_dir/Chart.yaml" ]; then
    check "$chart_name: Chart.yaml valid" helm show chart "$chart_dir"
  fi
  if [ -f "$chart_dir/values.yaml" ]; then
    check "$chart_name: values.yaml exists" test -f "$chart_dir/values.yaml"
  fi
done

# Report
echo "=== RESULTS ==="
echo -e "$RESULTS"
echo "PASS: $PASS  FAIL: $FAIL"
echo "Finished: $(date -Iseconds)"

# Save results
mkdir -p tests/results
echo -e "$(date -Iseconds)\n\n$RESULTS\nPASS: $PASS  FAIL: $FAIL" > tests/results/last_run.txt

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
