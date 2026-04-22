#!/usr/bin/env bash
# TESTS3_RELEASE_KEY_CONSISTENT — four-way single-key invariant (#229 A.3).
#
# From a release worktree, asserts that `release_id` from .current-stage
# matches:
#   (a) basename(worktree_root) == vexa-<release_id>
#   (b) current branch == release/<release_id>
#   (c) tests3/releases/<release_id>/ exists
# Exempted at stage=idle or when no .current-stage file (main checkout,
# fresh uninitialised worktree).
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
STAGE_FILE="$ROOT/tests3/.current-stage"

if [ ! -f "$STAGE_FILE" ]; then
    echo "ok: skipped (no .current-stage — uninitialised)"; exit 0
fi

STAGE=$(awk '/^stage:/ {gsub(/["'\'' ]/, "", $2); print $2; exit}' "$STAGE_FILE" 2>/dev/null || true)
REL=$(awk '/^release_id:/ {gsub(/["'\'' ]/, "", $2); print $2; exit}' "$STAGE_FILE" 2>/dev/null || true)

if [ "$STAGE" = "idle" ] || [ -z "$REL" ]; then
    echo "ok: skipped (stage=$STAGE, release=${REL:-none})"; exit 0
fi

fail=0

# (a) worktree basename
BASENAME=$(basename "$ROOT")
EXPECTED_BASENAME="vexa-${REL}"
if [ "$BASENAME" = "$EXPECTED_BASENAME" ]; then
    echo "ok: worktree basename = $BASENAME"
else
    echo "FAIL: worktree basename '$BASENAME' != expected '$EXPECTED_BASENAME'" >&2
    fail=1
fi

# (b) branch
BRANCH=$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)
EXPECTED_BRANCH="release/${REL}"
if [ "$BRANCH" = "$EXPECTED_BRANCH" ]; then
    echo "ok: branch = $BRANCH"
else
    echo "FAIL: branch '$BRANCH' != expected '$EXPECTED_BRANCH'" >&2
    fail=1
fi

# (c) release folder
REL_DIR="$ROOT/tests3/releases/$REL"
if [ -d "$REL_DIR" ]; then
    echo "ok: release dir tests3/releases/$REL/ exists"
else
    echo "FAIL: release dir $REL_DIR missing" >&2
    fail=1
fi

[ "$fail" -eq 0 ] || exit 1
echo "ok: all four surfaces agree on release_id=$REL"
