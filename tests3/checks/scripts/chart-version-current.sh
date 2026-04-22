#!/usr/bin/env bash
# CHART_VERSION_CURRENT — Chart.yaml.version ≥ latest v* git tag (#228 B.1).
#
# Reads deploy/helm/charts/vexa/Chart.yaml version, compares against the
# newest v* git tag via SemVer. Detects the stale-0.1.0 pattern.
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
CHART="$ROOT/deploy/helm/charts/vexa/Chart.yaml"

if [ ! -f "$CHART" ]; then
    echo "FAIL: $CHART missing" >&2; exit 1
fi

CHART_VER=$(awk '/^version:/ {gsub(/["'\'' ]/, "", $2); print $2; exit}' "$CHART")
if [ -z "$CHART_VER" ]; then
    echo "FAIL: no version: line in Chart.yaml" >&2; exit 1
fi

# Latest v*-prefixed git tag
LATEST_TAG=$(git -C "$ROOT" tag -l 'v*' | sort -V | tail -1)
if [ -z "$LATEST_TAG" ]; then
    echo "ok: chart version $CHART_VER (no v* tags to compare against)"; exit 0
fi
LATEST_VER=${LATEST_TAG#v}   # strip leading v

# SemVer compare (pure shell: split on '.', compare ints)
semver_ge() {
    # returns 0 iff $1 >= $2
    local a b
    IFS='.' read -ra a <<< "$1"
    IFS='.' read -ra b <<< "$2"
    for i in 0 1 2; do
        local x=${a[$i]:-0} y=${b[$i]:-0}
        # strip any pre-release suffix (e.g. 0.11.0-rc1 → 0)
        x=${x%%[!0-9]*}; y=${y%%[!0-9]*}
        x=${x:-0}; y=${y:-0}
        if [ "$x" -gt "$y" ]; then return 0; fi
        if [ "$x" -lt "$y" ]; then return 1; fi
    done
    return 0
}

if semver_ge "$CHART_VER" "$LATEST_VER"; then
    echo "ok: Chart.yaml version=$CHART_VER ≥ latest v* tag $LATEST_TAG"
else
    echo "FAIL: Chart.yaml version=$CHART_VER < latest v* tag $LATEST_TAG — chart is behind repo releases (#228)" >&2
    exit 1
fi
