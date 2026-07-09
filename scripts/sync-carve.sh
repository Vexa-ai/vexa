#!/usr/bin/env bash
# =============================================================================
# sync-carve.sh — publish the open-core subset of vexa-0.12 to a standalone repo
# =============================================================================
# One-way mirror: vexa-0.12 (this monorepo) is the single source of truth.
# Each run wipes the published repo's tracked content and re-copies the allowlist,
# then makes ONE DCO-signed commit. NOT a fork. Re-run whenever core/deploy/docs
# change. Commercial surface (clients/dashboard*, hosted ops, billing) is excluded.
#
# Usage:
#   scripts/sync-carve.sh [PUB_DIR]
#     PUB_DIR defaults to /home/dima/vexa-runtime (a clone of the standalone repo)
# Env:
#   DRY_RUN=1   copy + show status, do NOT commit/push (for local trials)
#   NO_PUSH=1   commit locally, skip git push
# =============================================================================
set -euo pipefail

SRC="${SRC:-/home/dima/vexa-0.12}"
PUB="${1:-${PUB:-/home/dima/vexa-runtime}}"

# --- the open-core allowlist (paths relative to SRC) -------------------------
INCLUDE=(
  # the four contributed pillars + their workspace
  core
  deploy
  integrations
  sdks
  tools
  packages
  scripts
  # public clients only — dashboard* stays commercial, excluded below
  clients/extension
  clients/slim
  clients/terminal
  clients/README.md
  # FINOS CALM architecture-as-code model (validated by gate:calm)
  calm
  # public docs (Mintlify-hosted) + generated CALM views (checked by gate:dataflow)
  docs/docs
  docs/views
  docs/README.md
  # root scaffolding required to build/run the workspace
  package.json
  pnpm-workspace.yaml
  pnpm-lock.yaml
  turbo.json
  tsconfig.base.json
  Makefile
  .dependency-cruiser.cjs
  .gitignore
  .dockerignore
  .githooks
  .github
  AGENTS.md
  README.md
  architecture.calm.json
  architecture.seal.json
  contracts.seal.json
  license-exceptions.json
)

RSYNC_EXCLUDES=(--exclude '.git' --exclude 'node_modules' --exclude '.turbo'
                --exclude '.claude' --exclude '.playwright-mcp' --exclude '.env'
                --exclude 'dist' --exclude '.next' --exclude '.venv' --exclude 'venv'
                --exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache'
                --exclude '.mypy_cache' --exclude '.ruff_cache' --exclude 'coverage'
                --exclude '*.so' --exclude '.DS_Store' --exclude 'build'
                --exclude 'sync-carve.sh')   # the carve tool itself stays private

# --- open-core-OWNED files (absent from SRC; maintained in the published repo) ---
# The FINOS governance + security-metadata layer lives ONLY in the published repo,
# never in SRC. A plain wipe deletes it and the allowlist copy below cannot
# re-create it, so it must be restored from the last published commit after wiping.
PRESERVE=(
  LICENSE
  NOTICE
  CONTRIBUTING.md
  CODE_OF_CONDUCT.md
  MAINTAINERS.md
  SECURITY.md
  security-insights.yml
  security   # OSPS baseline assessments (published-repo-owned dir)
  ARCHITECTURE.md   # OSPS-SA-01 root design doc (indexes CALM + docs; published-repo-owned)
)

echo "▶ source : $SRC"
echo "▶ publish: $PUB"
[ -d "$PUB/.git" ] || { echo "✗ $PUB is not a git repo — create + clone the standalone repo first"; exit 1; }

cd "$PUB"
# wipe everything tracked except .git, so deletions in SRC propagate
git ls-files -z | xargs -0 rm -f 2>/dev/null || true
# restore the open-core-owned governance layer (lives only here, not in SRC).
# One path per checkout: a single multi-path checkout is all-or-nothing — one
# missing path silently skipped restoring EVERYTHING (bit us 2026-07-03).
for p in "${PRESERVE[@]}"; do
  git checkout HEAD -- "$p" 2>/dev/null || echo "  ⚠ preserve: $p not in published HEAD"
done
find "$PUB" -mindepth 1 -name '.git' -prune -o -type d -empty -delete 2>/dev/null || true

# copy the allowlist, preserving layout
for p in "${INCLUDE[@]}"; do
  if [ ! -e "$SRC/$p" ]; then echo "  ⚠ skip (absent in source): $p"; continue; fi
  mkdir -p "$PUB/$(dirname "$p")"
  rsync -a "${RSYNC_EXCLUDES[@]}" "$SRC/$p" "$PUB/$(dirname "$p")/"
  echo "  ✓ $p"
done

# advance the carve checkpoint (the published repo records which SRC commit it mirrors)
mkdir -p "$PUB/.carve"
git -C "$SRC" rev-parse HEAD > "$PUB/.carve/checkpoint"

git add -A
SRC_SHA="$(git -C "$SRC" rev-parse --short HEAD)"

if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "── DRY_RUN: staged changes (no commit) ──"
  git status --short | head -40
  exit 0
fi

if git diff --cached --quiet; then
  echo "✓ no changes to publish"
  exit 0
fi

git commit -s -m "sync: carve open-core from vexa-0.12@${SRC_SHA}"
echo "✓ committed (DCO-signed) from vexa-0.12@${SRC_SHA}"

[ "${NO_PUSH:-0}" = "1" ] && { echo "↳ NO_PUSH set — not pushing"; exit 0; }
git push origin HEAD
echo "✓ pushed"
