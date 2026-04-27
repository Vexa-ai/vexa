#!/usr/bin/env bash
# CHART_VERSION_CURRENT — Chart.yaml.version == latest vexa-X.Y.Z git tag (#228 B.1).
#
# Policy: chart version INHERITS from the most recent repo release tag.
# Never ahead (would conflict with future tags), never behind (would
# reproduce the #228 drift). When a new vexa-X.Y.Z tag is cut, the same
# commit bumps Chart.yaml.version to match. Equality is the invariant.
#
# Reads deploy/helm/charts/vexa/Chart.yaml version, compares for exact
# equality against the newest vexa-X.Y.Z git tag. Fails loud on any mismatch.
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

# Latest vexa-prefixed git tag (the actual release-tag convention in this
# repo). v0.10.5 R1 fix: earlier code used `git tag -l 'v*'` which
# accidentally matched `vexa-X.Y.Z` because the `v*` glob = literal `v`
# + anything. After matching, `${LATEST_TAG#v}` stripped only the
# leading `v` → `exa-X.Y.Z` (gibberish), guaranteeing the equality test
# always failed even when Chart.yaml + tag agreed.
LATEST_TAG=$(git -C "$ROOT" tag -l 'vexa-[0-9]*' | sort -V | tail -1)
if [ -z "$LATEST_TAG" ]; then
    echo "ok: chart version $CHART_VER (no vexa-* tags to compare against)"; exit 0
fi
LATEST_VER=${LATEST_TAG#vexa-}   # strip vexa- prefix → bare semver
# Defensive: the strip should leave a bare X.Y.Z. If it doesn't, the
# tag-naming convention has drifted; fail loud rather than silently
# comparing junk.
if ! [[ "$LATEST_VER" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.+-].*)?$ ]]; then
    echo "FAIL: latest tag '$LATEST_TAG' does not strip cleanly to semver (got '$LATEST_VER')" >&2
    exit 1
fi

if [ "$CHART_VER" = "$LATEST_VER" ]; then
    echo "ok: Chart.yaml version=$CHART_VER matches latest tag $LATEST_TAG"
else
    echo "FAIL: Chart.yaml version=$CHART_VER != latest tag $LATEST_TAG (stripped to $LATEST_VER) — chart is not inheriting current release version (#228)" >&2
    exit 1
fi
