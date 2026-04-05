#!/usr/bin/env bash
set -euo pipefail

# sync-clean.sh — Sync the 'clean' branch from the working branch
#
# Merges source branch into clean (preserving commit history),
# then strips dev-only paths listed in .cleanignore.
#
# First sync: brings full history. Subsequent syncs: incremental.
# The PR to main can be squash-merged if you want a single commit there.
#
# Usage:
#   ./scripts/sync-clean.sh                              # sync from current branch
#   ./scripts/sync-clean.sh feature/agentic-runtime      # sync from specific branch
#   ./scripts/sync-clean.sh --push                       # sync and push
#   ./scripts/sync-clean.sh --reset                      # recreate clean from scratch

SOURCE_BRANCH=""
PUSH=false
RESET=false

for arg in "$@"; do
  case "$arg" in
    --push) PUSH=true ;;
    --reset) RESET=true ;;
    -*) echo "Unknown flag: $arg"; exit 1 ;;
    *) SOURCE_BRANCH="$arg" ;;
  esac
done

SOURCE_BRANCH="${SOURCE_BRANCH:-$(git branch --show-current)}"

if [ "$SOURCE_BRANCH" = "clean" ]; then
  echo "Error: already on clean branch. Switch to your working branch first."
  exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
CLEANIGNORE="$REPO_ROOT/.cleanignore"
if [ ! -f "$CLEANIGNORE" ]; then
  echo "Error: .cleanignore not found at $CLEANIGNORE"
  exit 1
fi

# Read paths from .cleanignore (skip comments and blank lines)
EXCLUDE_PATHS=()
while IFS= read -r line; do
  line="${line%%#*}"        # strip inline comments
  line="${line// /}"        # trim whitespace
  [ -z "$line" ] && continue
  EXCLUDE_PATHS+=("$line")
done < "$CLEANIGNORE"

if [ ${#EXCLUDE_PATHS[@]} -eq 0 ]; then
  echo "Error: no paths found in .cleanignore"
  exit 1
fi

echo "=== Syncing clean branch from $SOURCE_BRANCH ==="
echo "Paths to exclude: ${#EXCLUDE_PATHS[@]}"

# Stash any uncommitted work
STASHED=false
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Stashing uncommitted changes..."
  git stash push -m "sync-clean: auto-stash"
  STASHED=true
fi

# Fetch latest main
git fetch origin main --quiet 2>/dev/null || true

# Handle --reset: delete and recreate clean branch
if [ "$RESET" = true ]; then
  echo "Resetting clean branch..."
  git branch -D clean 2>/dev/null || true
fi

# Switch to clean branch (or create from main)
if git rev-parse --verify clean >/dev/null 2>&1; then
  git checkout clean
else
  echo "Creating clean branch from origin/main..."
  git checkout -b clean origin/main
fi

# Merge the source branch (preserves history)
echo "Merging $SOURCE_BRANCH..."
if ! git merge "$SOURCE_BRANCH" --no-edit -X theirs 2>/dev/null; then
  # Resolve any remaining conflicts by accepting theirs
  CONFLICTED=$(git diff --name-only --diff-filter=U 2>/dev/null || true)
  if [ -n "$CONFLICTED" ]; then
    echo "Resolving $(echo "$CONFLICTED" | wc -l) conflicts (accepting source branch)..."
    echo "$CONFLICTED" | while IFS= read -r f; do
      git checkout --theirs "$f" 2>/dev/null && git add "$f" 2>/dev/null || git rm "$f" 2>/dev/null || true
    done
    git commit --no-edit 2>/dev/null || true
  fi
fi

# Strip excluded paths
REMOVED=0
for pattern in "${EXCLUDE_PATHS[@]}"; do
  MATCHES=$(git ls-files --cached "$pattern" 2>/dev/null || true)
  if [ -n "$MATCHES" ]; then
    echo "$MATCHES" | xargs git rm -r --cached --quiet --force 2>/dev/null || true
    echo "$MATCHES" | xargs rm -rf 2>/dev/null || true
    COUNT=$(echo "$MATCHES" | wc -l)
    REMOVED=$((REMOVED + COUNT))
  fi
done

# Commit the strip (if anything was removed)
if git diff --cached --quiet; then
  echo "Nothing to strip (clean branch is up to date)"
else
  echo "Stripped $REMOVED dev-only files"
  git commit -m "chore: strip dev artifacts via .cleanignore ($REMOVED files)"
fi

echo ""
echo "=== Clean branch status ==="
AHEAD=$(git log --oneline origin/main..clean | wc -l)
FIRST_PARENT=$(git log --oneline --first-parent origin/main..clean | wc -l)
echo "Commits ahead of main: $AHEAD (first-parent: $FIRST_PARENT)"
git log --oneline --first-parent -5 clean

if [ "$PUSH" = true ]; then
  echo ""
  echo "Pushing clean to origin..."
  git push origin clean
fi

# Return to source branch
git checkout "$SOURCE_BRANCH"

# Restore stash if we stashed
if [ "$STASHED" = true ]; then
  echo "Restoring stashed changes..."
  git stash pop
fi

echo ""
echo "Done. You're back on $SOURCE_BRANCH."
