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

# release_label caps total length at 32 chars (Linode LKE constraint —
# see common.sh:302) and TRUNCATES the release_id segment when needed.
# So the assertion must accept ANY non-empty prefix of $EXPECTED, not
# the whole string.
#
# Build the matcher: PREFIX-<release_id-or-prefix-thereof>-<6 hex>$
# where the release_id portion is one or more characters from the start
# of $EXPECTED.
expected_chars=${#EXPECTED}
prefix_chars=${#PREFIX}
sfx_chars=6
# Available chars for release_id segment after prefix + 2 separators + suffix
max_rel=$((32 - prefix_chars - sfx_chars - 2))
if [ "$max_rel" -lt 1 ]; then max_rel=$expected_chars; fi  # very unusual prefix
# The actual release_id segment in the label must equal the first
# min($expected_chars, $max_rel) characters of $EXPECTED.
target_rel_len=$expected_chars
if [ "$target_rel_len" -gt "$max_rel" ]; then target_rel_len=$max_rel; fi
expected_rel_segment=${EXPECTED:0:$target_rel_len}

pattern="^${PREFIX}-${expected_rel_segment}-[0-9a-f]{6}$"
if ! [[ "$L1" =~ $pattern ]]; then
    echo "FAIL: '$L1' does not match $pattern (expected_rel_segment=$expected_rel_segment, full release_id=$EXPECTED)" >&2; exit 1
fi
if ! [[ "$L2" =~ $pattern ]]; then
    echo "FAIL: '$L2' does not match $pattern (expected_rel_segment=$expected_rel_segment, full release_id=$EXPECTED)" >&2; exit 1
fi

# Suffixes differ
S1=${L1##*-}; S2=${L2##*-}
if [ "$S1" = "$S2" ]; then
    echo "FAIL: suffix collision ($S1 == $S2)" >&2; exit 1
fi

trunc_note=""
if [ "$target_rel_len" -lt "$expected_chars" ]; then
    trunc_note=" (release_id truncated $expected_chars→$target_rel_len for 32-char label cap)"
fi
echo "ok: labels carry release_id=$expected_rel_segment with distinct suffixes ($S1, $S2)$trunc_note"
