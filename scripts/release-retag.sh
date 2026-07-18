#!/usr/bin/env bash
# release-retag (#583 item 3) — the scripted, guarded re-tag.
#
# A failed release run gets fixed on main and the SAME version tag must move to main's new
# head. v0.12.2 did this by hand 3× under pressure, each with an ad-hoc "grep the fix on
# main's head before moving the tag" check. This script is that operator ritual, mechanized:
#
#   make release-retag VERSION=v0.12.14 EXPECT=<sha-of-the-fix>            # dry-run: plan only
#   make release-retag VERSION=v0.12.14 EXPECT=<sha-of-the-fix> CONFIRM=1  # act + watch
#
# Guards, in order:
#   1. VERSION must be an existing tag (a retag moves a tag; minting a new one is `git tag` +
#      release-images.yml's own preflight, not this script).
#   2. EXPECT is REQUIRED and must be an ancestor of (or equal to) origin/main's head — the
#      machine form of "the fix is actually on what I'm about to tag". No EXPECT, no retag.
#   3. Dry-run by default: prints old → new tag targets and the exact commands. CONFIRM=1 pushes.
# After the push it watches the release-images run for the tag (gh CLI, best-effort).
set -euo pipefail

VERSION="${VERSION:-}"; EXPECT="${EXPECT:-}"; CONFIRM="${CONFIRM:-}"; REMOTE="${REMOTE:-origin}"
[ -n "$VERSION" ] || { echo "ERROR: VERSION=vX.Y.Z[-suffix] is required"; exit 1; }
[ -n "$EXPECT" ]  || { echo "ERROR: EXPECT=<commit that must be on main's head> is required — the guard is the point"; exit 1; }

echo "Fetching $REMOTE (tags + main)…"
git fetch --quiet "$REMOTE" main "+refs/tags/$VERSION:refs/tags/$VERSION" 2>/dev/null || git fetch --quiet "$REMOTE" main

OLD=$(git rev-parse -q --verify "refs/tags/$VERSION^{commit}" || true)
[ -n "$OLD" ] || { echo "ERROR: tag $VERSION does not exist — retag moves an existing tag; mint new tags via the normal release path"; exit 1; }

HEAD_MAIN=$(git rev-parse "$REMOTE/main")
EXPECT_SHA=$(git rev-parse -q --verify "$EXPECT^{commit}" || true)
[ -n "$EXPECT_SHA" ] || { echo "ERROR: EXPECT ($EXPECT) is not a commit in this repo"; exit 1; }
git merge-base --is-ancestor "$EXPECT_SHA" "$HEAD_MAIN" \
  || { echo "ERROR: expected commit $EXPECT_SHA is NOT on $REMOTE/main ($HEAD_MAIN) — the fix you are retagging for has not landed; refusing"; exit 1; }

echo ""
echo "Retag plan for $VERSION:"
echo "  old tag target : $OLD  ($(git log -1 --format=%s "$OLD"))"
echo "  new tag target : $HEAD_MAIN  ($(git log -1 --format=%s "$HEAD_MAIN"))"
echo "  guard          : $EXPECT_SHA is an ancestor of $REMOTE/main ✓"
if [ "$OLD" = "$HEAD_MAIN" ]; then echo "  NOTE: tag already points at $REMOTE/main's head — nothing to move"; exit 0; fi

if [ -z "$CONFIRM" ]; then
  echo ""
  echo "DRY-RUN (pass CONFIRM=1 to execute):"
  echo "  git push $REMOTE :refs/tags/$VERSION"
  echo "  git tag -f $VERSION $HEAD_MAIN && git push $REMOTE refs/tags/$VERSION"
  exit 0
fi

echo ""
echo "Moving $VERSION → $HEAD_MAIN…"
git push "$REMOTE" ":refs/tags/$VERSION"
git tag -f "$VERSION" "$HEAD_MAIN"
git push "$REMOTE" "refs/tags/$VERSION"
echo "  ✓ tag moved"

if command -v gh >/dev/null 2>&1; then
  echo "Waiting for the release-images run on $VERSION…"
  for i in $(seq 1 12); do
    RUN_ID=$(gh run list --workflow release-images.yml --branch "$VERSION" --limit 1 --json databaseId,createdAt \
      -q '.[0].databaseId' 2>/dev/null || true)
    [ -n "$RUN_ID" ] && break; sleep 10
  done
  if [ -n "${RUN_ID:-}" ]; then gh run watch "$RUN_ID" --exit-status; else
    echo "  (no run appeared within 2 min — watch manually: gh run list --workflow release-images.yml)"; fi
else
  echo "  gh CLI not found — watch the tag build manually in Actions."
fi
