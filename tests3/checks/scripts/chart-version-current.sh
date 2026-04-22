#!/usr/bin/env bash
# CHART_VERSION_CURRENT — Chart.yaml.version == latest v* git tag (#228 B.1).
#
# Policy: chart version INHERITS from the most recent repo release tag.
# Never ahead (would conflict with future tags), never behind (would
# reproduce the #228 drift). When a new v* tag is cut, the same commit
# bumps Chart.yaml.version to match. Equality is the invariant.
#
# Reads deploy/helm/charts/vexa/Chart.yaml version, compares for exact
# equality against the newest v* git tag. Fails loud on any mismatch.
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

if [ "$CHART_VER" = "$LATEST_VER" ]; then
    echo "ok: Chart.yaml version=$CHART_VER matches latest v* tag $LATEST_TAG"
else
    echo "FAIL: Chart.yaml version=$CHART_VER != latest v* tag $LATEST_TAG — chart is not inheriting current release version (#228)" >&2
    exit 1
fi
