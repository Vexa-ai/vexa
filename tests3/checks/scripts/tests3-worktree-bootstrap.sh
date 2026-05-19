#!/usr/bin/env bash
# TESTS3_WORKTREE_BOOTSTRAP — verify release-worktree end-to-end (#229 A.2).
#
# Creates a sandbox worktree ../vexa-check-<rand>, asserts:
#   (a) target dir exists
#   (b) tests3/.current-stage seeded at stage: idle, release_id: <test_id>
#   (c) branch release/<test_id> exists
#   (d) stage.py next from the new worktree prints `groom`
# Cleans up unconditionally.
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
PARENT=$(dirname "$ROOT")
TEST_ID="check-$(openssl rand -hex 4)"
TARGET="$PARENT/vexa-${TEST_ID}"
BRANCH="release/${TEST_ID}"

cleanup() {
    git -C "$ROOT" worktree remove --force "$TARGET" >/dev/null 2>&1 || true
    git -C "$ROOT" branch -D "$BRANCH" >/dev/null 2>&1 || true
    rm -rf "$TARGET" 2>/dev/null || true
}
trap cleanup EXIT

# Dispatch the same helper the Makefile target uses, to keep one code path.
bash "$ROOT/tests3/lib/worktree.sh" create "$TEST_ID" >/dev/null

fail=0

# (a)
if [ -d "$TARGET" ]; then
    echo "ok: target dir created at $TARGET"
else
    echo "FAIL: target dir $TARGET not created" >&2; fail=1
fi

# (b)
STAGE_FILE="$TARGET/tests3/.current-stage"
if [ -f "$STAGE_FILE" ]; then
    STAGE=$(awk '/^stage:/ {gsub(/["'\'' ]/, "", $2); print $2; exit}' "$STAGE_FILE")
    REL=$(awk '/^release_id:/ {gsub(/["'\'' ]/, "", $2); print $2; exit}' "$STAGE_FILE")
    if [ "$STAGE" = "idle" ] && [ "$REL" = "$TEST_ID" ]; then
        echo "ok: .current-stage seeded at stage=idle, release_id=$TEST_ID"
    else
        echo "FAIL: .current-stage shows stage=$STAGE, release_id=$REL" >&2; fail=1
    fi
else
    echo "FAIL: $STAGE_FILE missing" >&2; fail=1
fi

# (c)
if git -C "$ROOT" show-ref --quiet "refs/heads/${BRANCH}"; then
    echo "ok: branch $BRANCH exists"
else
    echo "FAIL: branch $BRANCH missing" >&2; fail=1
fi

# (d)
if [ -d "$TARGET" ]; then
    NEXT=$(python3 "$TARGET/tests3/lib/stage.py" next | tr -d '[:space:]')
    if [ "$NEXT" = "groom" ]; then
        echo "ok: stage.py next reports groom"
    else
        echo "FAIL: stage.py next reports '$NEXT', expected 'groom'" >&2; fail=1
    fi
fi

[ "$fail" -eq 0 ] || exit 1
echo "ok: release-worktree bootstrap end-to-end"
