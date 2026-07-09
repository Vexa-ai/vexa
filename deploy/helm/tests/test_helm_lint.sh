#!/usr/bin/env bash
# Structural + lint check for the v0.12 vexa chart (no cluster required).
# Skips lint gracefully if helm is absent.
set -euo pipefail

HELM_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CHART="$HELM_DIR/charts/vexa"

echo "=== Helm chart structure (vexa v0.12) ==="
for f in Chart.yaml values.yaml values-test.yaml templates; do
  [ -e "$CHART/$f" ] || { echo "FAIL: missing $f"; exit 1; }
  echo "  OK: $f"
done

if command -v helm >/dev/null 2>&1; then
  echo "  Linting (default values)..."
  helm lint "$CHART"
  echo "  Linting (values-test)..."
  helm lint "$CHART" -f "$CHART/values-test.yaml"
else
  echo "  SKIP: helm not installed"
fi

echo "Helm chart lint: PASS"
