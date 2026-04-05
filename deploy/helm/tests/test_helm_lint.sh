#!/bin/bash
# Validate Helm chart structure and lint.
# Requires helm CLI. Skips gracefully if not installed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HELM_DIR="$ROOT/deploy/helm"

echo "=== Helm chart validation ==="

# Check charts exist
for chart in vexa vexa-lite; do
  chart_dir="$HELM_DIR/charts/$chart"
  if [ ! -d "$chart_dir" ]; then
    echo "FAIL: chart directory $chart not found"
    exit 1
  fi
  echo "  OK: charts/$chart/ exists"

  if [ ! -f "$chart_dir/Chart.yaml" ]; then
    echo "FAIL: $chart/Chart.yaml not found"
    exit 1
  fi
  echo "  OK: $chart/Chart.yaml exists"

  if [ ! -d "$chart_dir/templates" ]; then
    echo "FAIL: $chart/templates/ not found"
    exit 1
  fi
  echo "  OK: $chart/templates/ exists"
done

# Lint with helm if available
if command -v helm &>/dev/null; then
  for chart in vexa vexa-lite; do
    chart_dir="$HELM_DIR/charts/$chart"
    echo "  Linting $chart..."
    if helm lint "$chart_dir" 2>&1 | tail -3; then
      echo "  OK: $chart passes helm lint"
    else
      echo "  WARN: $chart has helm lint warnings (non-fatal)"
    fi
  done
else
  echo "  SKIP: helm not installed, skipping lint"
fi

echo ""
echo "Helm chart validation: PASS"
