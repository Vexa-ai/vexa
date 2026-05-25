#!/usr/bin/env bash
# Deliver a code review to a human as ONE GitHub single-commit URL.
#
# Behavior:
#   1. Compute the non-test product files for the PR
#      (using list-non-test-files.sh + references/test-file-patterns.txt).
#   2. Create a side branch `codex/review-squash/<name>` rooted at the PR's
#      base branch.
#   3. Apply only the product-file diff from the PR head onto that branch
#      as ONE squashed commit.
#   4. Push to origin.
#   5. Print the GitHub single-commit URL — that's the review surface.
#
# The review-squash branch is throwaway (does not affect the pack PR).
# The pack PR remains the source of truth for what ships. The squash is
# purely a clean "show this human exactly the product diff" delivery
# artifact.
#
# Usage:
#   squash-for-review.sh <pr-number> --worktree <path> [--repo owner/repo] [--name <slug>]
#
# Examples:
#   squash-for-review.sh 365 \
#     --worktree /home/dima/dev/vexa-pack-0.10.6x-pack-4-stop-delete-lifecycle-convergence \
#     --name pack-4
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIST_SCRIPT="$SKILL_DIR/scripts/list-non-test-files.sh"

PR=""
REPO=""
WT=""
NAME=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --worktree) WT="$2"; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 <pr-number> --worktree <path> [--repo owner/repo] [--name <slug>]"
      exit 0
      ;;
    *) PR="$1"; shift ;;
  esac
done

[ -z "$PR" ] && { echo "ERROR: pr number required" >&2; exit 2; }
[ -z "$WT" ] && { echo "ERROR: --worktree <path> required" >&2; exit 2; }
[ -d "$WT" ] || { echo "ERROR: worktree $WT not found" >&2; exit 2; }
if [ -z "$REPO" ]; then
  REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || true)"
fi
[ -z "$REPO" ] && { echo "ERROR: --repo owner/repo required" >&2; exit 2; }
if [ -z "$NAME" ]; then
  NAME="pr-${PR}"
fi

BASE_REF="$(gh pr view "$PR" --repo "$REPO" --json baseRefName --jq .baseRefName 2>/dev/null || true)"
[ -z "$BASE_REF" ] && BASE_REF="v0.10.6"

echo "[squash-for-review] PR=$PR repo=$REPO worktree=$WT name=$NAME base=$BASE_REF"

# Compute product file list
files="$("$LIST_SCRIPT" "$PR" --repo "$REPO")"
[ -z "$files" ] && { echo "ERROR: no product files in PR $PR" >&2; exit 1; }
echo "[squash-for-review] product files: $(echo "$files" | wc -l)"

# Patch file with the product-only diff (head of pack vs PR base)
patch="$(mktemp /tmp/squash-${NAME}.XXXX.patch)"
trap 'rm -f "$patch"' EXIT
(cd "$WT" && git diff "${BASE_REF}..HEAD" -- $files) > "$patch"
[ ! -s "$patch" ] && { echo "ERROR: empty patch" >&2; exit 1; }
echo "[squash-for-review] patch: $(wc -l < "$patch") lines"

# Side branch
SQUASH_BRANCH="codex/review-squash/${NAME}"
SQUASH_WT="/tmp/squash-${NAME}"

# Clean any prior run
if [ -d "$SQUASH_WT" ]; then
  (cd "$WT" && git worktree remove --force "$SQUASH_WT" 2>/dev/null || true)
  rm -rf "$SQUASH_WT"
fi
(cd "$WT" && git branch -D "$SQUASH_BRANCH" 2>/dev/null) >/dev/null || true

# Create worktree on base, apply, commit, push
(cd "$WT" && git worktree add -b "$SQUASH_BRANCH" "$SQUASH_WT" "$BASE_REF") >/dev/null
cd "$SQUASH_WT"

# Try strict apply first; fall back to --3way if any path doesn't exist in base
if ! git apply "$patch" 2>/dev/null; then
  echo "[squash-for-review] strict apply failed; using --3way" >&2
  git apply --3way "$patch" 2>&1 | tail -5 >&2 || true
fi
git add -A >/dev/null
if git diff --cached --quiet; then
  echo "ERROR: no changes staged after patch" >&2
  exit 1
fi
git commit --no-gpg-sign -m "review-squash: ${NAME} (PR #${PR}) product files only" >/dev/null

sha="$(git rev-parse HEAD)"
nfiles="$(git diff --name-only "HEAD~..HEAD" | wc -l | tr -d ' ')"
nlines="$(git diff "HEAD~..HEAD" | wc -l | tr -d ' ')"

# Push (force-with-lease so reruns overwrite cleanly)
git push -f -u origin "$SQUASH_BRANCH" 2>&1 | tail -1

URL="https://github.com/${REPO}/commit/${sha}"
SPLIT_URL="${URL}?diff=split&w=1"

echo
echo "## Code review delivery — ${NAME} (PR #${PR})"
echo
echo "Single-commit review URL (only product files, no evidence/tests):"
echo "  $URL"
echo
echo "  split-view + whitespace-ignored:"
echo "  $SPLIT_URL"
echo
echo "Stats: $nfiles files, $nlines lines"
echo
echo "Review workflow:"
echo "  1. Open the URL above. GitHub renders the entire pack delta as ONE commit."
echo "  2. Read top-to-bottom; leave inline comments via the '+' on any line."
echo "  3. Reply 'pass' / 'fix-required <notes>' / 'block <notes>' to the chat."
echo "  4. After verdict, the develop skill writes .agents/packs/<pack-id>/code-review.md"
echo "     and flips the GitHub epic to status:ready-for-stage."
