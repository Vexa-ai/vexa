#!/usr/bin/env bash
# carve/_apply_layer.sh — apply the carve-owned override layer in a target dir.
# Shared by seed.sh and sync.sh: copies override files, runs the transform hook,
# and (until docs are committed upstream) lays docs/docs from the mono worktree.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/manifest.sh"
DEST="$1"

# 1. carve-owned override files
for entry in "${CARVE_OVERRIDES[@]}"; do
  src="${entry%%:*}"; dst="${entry##*:}"
  mkdir -p "$DEST/$(dirname "$dst")"
  cp "$HERE/overrides/$src" "$DEST/$dst"
done

# 2. deterministic transforms
[ -x "$CARVE_TRANSFORM" ] && ( cd "$DEST" && "$CARVE_TRANSFORM" )

# 3. docs from worktree (temporary, until docs/docs is committed upstream)
if [ "${CARVE_DOCS_FROM_WORKTREE:-0}" = "1" ] && [ -d "$MONO/docs/docs" ]; then
  mkdir -p "$DEST/docs"
  rsync -a --delete --exclude '.git' --exclude '*.log' "$MONO/docs/docs" "$DEST/docs/"
fi
