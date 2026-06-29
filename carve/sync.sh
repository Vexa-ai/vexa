#!/usr/bin/env bash
# =============================================================================
# carve/sync.sh — INCREMENTAL, append-only publish of new mono work to vexa-core
# as a reviewable PR. NEVER rewrites history; safe once external contributors exist.
#
# Reads .carve/checkpoint (last mono SHA published) from vexa-core. Replays every
# mono commit in checkpoint..SRC_BRANCH that touches an INCLUDE path, materializing
# the carved tree per commit and re-committing with the ORIGINAL (mailmapped)
# author/date/message + Signed-off-by — so the contributor graph keeps growing.
# Pushes a branch and opens a PR.
#
# Usage:  carve/sync.sh [--push]
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/manifest.sh"

# fresh clone of the published repo (so we never disturb a working copy)
WORK="$(mktemp -d)/carve"
git clone -q "$CARVE_REMOTE" "$WORK"
cd "$WORK"
CKPT="$(cat .carve/checkpoint 2>/dev/null || true)"
[ -n "$CKPT" ] || { echo "✗ no .carve/checkpoint in vexa-core — run seed.sh first"; exit 1; }

HEAD_SHA="$(git -C "$MONO" rev-parse "$SRC_BRANCH")"
SHORT="$(git -C "$MONO" rev-parse --short "$SRC_BRANCH")"
BR="carve/sync-$(git -C "$MONO" rev-parse --short "$CKPT")-to-$SHORT"
git checkout -q -b "$BR"

# commits since checkpoint touching INCLUDE paths, oldest first, no merges
mapfile -t COMMITS < <(git -C "$MONO" rev-list --reverse --no-merges \
  "$CKPT..$HEAD_SHA" -- "${CARVE_INCLUDE[@]}")
echo "▶ $((${#COMMITS[@]})) candidate commit(s) since ${CKPT:0:7}"
[ ${#COMMITS[@]} -eq 0 ] && { echo "✓ nothing to sync (carve already at $SHORT)"; exit 0; }

replayed=0
for C in "${COMMITS[@]}"; do
  # wipe the carved paths in the work tree (so deletions propagate)
  for p in "${CARVE_INCLUDE[@]}"; do rm -rf "$WORK/${p:?}"; done
  # materialize commit C's version of each INCLUDE path
  for p in "${CARVE_INCLUDE[@]}"; do
    git -C "$MONO" ls-tree -r --name-only "$C" -- "$p" >/dev/null 2>&1 || continue
    if git -C "$MONO" cat-file -e "$C:$p" 2>/dev/null; then
      mkdir -p "$WORK/$(dirname "$p")"
      ( cd "$MONO" && git archive "$C" -- "$p" 2>/dev/null ) | tar -x -C "$WORK" 2>/dev/null || true
    fi
  done
  # purge EXCLUDE sub-paths, then apply override layer
  for p in "${CARVE_EXCLUDE[@]}"; do rm -rf "$WORK/$p"; done
  "$HERE/_apply_layer.sh" "$WORK"

  git add -A
  git diff --cached --quiet && continue   # commit touched only non-carved/excluded files
  AUTHOR="$(git -c mailmap.file="$CARVE_MAILMAP" -C "$MONO" show -s --format='%aN <%aE>' --use-mailmap "$C")"
  ADATE="$(git -C "$MONO" show -s --format='%aI' "$C")"
  MSG="$(git -C "$MONO" show -s --format='%B' "$C")"
  GIT_COMMITTER_NAME="Dmitry Grankin" GIT_COMMITTER_EMAIL="dmitry@vexa.ai" \
    git commit -q -s --author="$AUTHOR" --date="$ADATE" -m "$MSG"
  replayed=$((replayed+1))
done

# advance checkpoint
echo "$HEAD_SHA" > .carve/checkpoint
git add .carve/checkpoint
git commit -q -s -m "carve: advance checkpoint → $SHORT" \
  --author="Dmitry Grankin <dmitry@vexa.ai>"
echo "✓ replayed $replayed commit(s); checkpoint → $SHORT"

if [ "${1:-}" = "--push" ]; then
  git push -u origin "$BR"
  gh pr create --repo Vexa-ai/vexa-core --base main --head "$BR" \
    --title "carve: sync open-core to vexa-0.12@$SHORT" \
    --body "Append-only carve sync via \`carve/sync.sh\`. $replayed commit(s) replayed with original authorship; checkpoint advanced to \`$SHORT\`. Reproducible from \`carve/manifest.sh\`."
  echo "✓ branch pushed + PR opened"
else
  echo "ℹ dry run at $WORK — re-run with --push to open the PR"
fi
