#!/usr/bin/env bash
# TESTS3_LABEL_RELEASE_TRACEABLE — release-aware infra label (#229).
#
# Source common.sh, call release_label twice with a test prefix, assert:
#   (a) each output matches ^PREFIX-<release_id|adhoc>-[0-9a-f]{6}$
#   (b) both calls produce distinct 6-hex suffixes
#   (c) release_id segment matches .current-stage (or "adhoc" fallback)
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
# shellcheck disable=SC1091
source "$ROOT/tests3/lib/common.sh"

PREFIX=vexa-t3-check
L1=$(release_label "$PREFIX")
L2=$(release_label "$PREFIX")

# Expected release_id (from .current-stage, or "adhoc")
EXPECTED=$(awk '/^release_id:/ {gsub(/["'\'' ]/, "", $2); print $2; exit}' \
    "$ROOT/tests3/.current-stage" 2>/dev/null || true)
EXPECTED="${EXPECTED:-adhoc}"

pattern="^${PREFIX}-${EXPECTED}-[0-9a-f]{6}$"
if ! [[ "$L1" =~ $pattern ]]; then
    echo "FAIL: '$L1' does not match $pattern" >&2; exit 1
fi
if ! [[ "$L2" =~ $pattern ]]; then
    echo "FAIL: '$L2' does not match $pattern" >&2; exit 1
fi

# Suffixes differ
S1=${L1##*-}; S2=${L2##*-}
if [ "$S1" = "$S2" ]; then
    echo "FAIL: suffix collision ($S1 == $S2)" >&2; exit 1
fi

echo "ok: labels carry release_id=$EXPECTED with distinct suffixes ($S1, $S2)"
