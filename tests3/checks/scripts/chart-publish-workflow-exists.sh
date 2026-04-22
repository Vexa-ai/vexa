#!/usr/bin/env bash
# CHART_PUBLISH_WORKFLOW_EXISTS — gh-pages Helm repo publish workflow wired (#228 B.2).
#
# Asserts:
#   (a) .github/workflows/chart-release.yml exists
#   (b) contains a reference to helm/chart-releaser-action
#   (c) parses as valid YAML
# Does NOT assert that any publish actually ran (that's post-ship).
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
WF="$ROOT/.github/workflows/chart-release.yml"

if [ ! -f "$WF" ]; then
    echo "FAIL: $WF missing — chart publish workflow not wired (#228)" >&2; exit 1
fi

if ! grep -q "helm/chart-releaser-action" "$WF"; then
    echo "FAIL: $WF does not reference helm/chart-releaser-action" >&2; exit 1
fi

if ! python3 -c "import yaml,sys; yaml.safe_load(open('$WF'))" 2>/dev/null; then
    echo "FAIL: $WF is not valid YAML" >&2; exit 1
fi

echo "ok: chart-release.yml exists + references helm/chart-releaser-action + parses"
