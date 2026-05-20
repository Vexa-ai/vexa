#!/usr/bin/env bash
# CHART_VERSION_CURRENT — Chart.yaml.version inherits latest vexa release tag (#228 B.1).
#
# Policy: chart version INHERITS from the most recent repo release tag.
# Never ahead (would conflict with future tags), never behind (would
# reproduce the #228 drift). When a new vexa-X.Y.Z tag is cut, the same
# commit bumps Chart.yaml.version to match. Equality is the invariant after
# normalizing four-component app releases (X.Y.Z.N) into Helm SemVer build
# metadata (X.Y.Z+N); Helm chart versions cannot use four numeric segments.
#
# Reads deploy/helm/charts/vexa/Chart.yaml version, compares against the
# newest vexa-X.Y.Z[.N] git tag after Helm SemVer normalization. Fails loud on
# any mismatch.
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

# Latest release tag. Historical releases used both `vexa-X.Y.Z` and `vX.Y.Z`
# conventions, so normalize both and choose by semantic version.
LATEST_TAG=$(
    {
        git -C "$ROOT" tag -l 'vexa-[0-9]*'
        git -C "$ROOT" tag -l 'v[0-9]*'
    } | awk '
        /^vexa-/ { print substr($0, 6) "\t" $0; next }
        /^v[0-9]/ { print substr($0, 2) "\t" $0; next }
    ' | sort -V | tail -1 | cut -f2-
)
if [ -z "$LATEST_TAG" ]; then
    echo "ok: chart version $CHART_VER (no release tags to compare against)"; exit 0
fi
LATEST_VER=${LATEST_TAG#vexa-}
LATEST_VER=${LATEST_VER#v}
# Defensive: the strip should leave a bare X.Y.Z. If it doesn't, the
# tag-naming convention has drifted; fail loud rather than silently
# comparing junk.
if [[ "$LATEST_VER" =~ ^([0-9]+\.[0-9]+\.[0-9]+)\.([0-9]+)$ ]]; then
    LATEST_VER="${BASH_REMATCH[1]}+${BASH_REMATCH[2]}"
fi

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
